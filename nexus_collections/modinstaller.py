import sys
import os
import glob
import shutil
import time
import json
import mobase
import subprocess

from nxmhandler.utils import PYTHON_ENV
from .utils import write_all, ask_yes_no, logger, extract_all, DATA_PATH
from .v20 import BROWSERS

if "PyQt6" in sys.modules:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QPushButton

from curl_cffi.requests.exceptions import RequestException
from lxml import etree

class ModInstaller:
    def __init__(self, plugin: "NexusCollections", mods, load_order, plugins_state, plugin_rules):
        self._callbacks_connected = None
        self.__plugin    = plugin
        self.load_order  = load_order
        self.plugins_state = plugins_state
        self.plugin_rules = plugin_rules
        self._session        = None
        self._session_expiry = None
        self._on_finish      = None

        self._mods = [
            {
                "mod":           m,
                "index":         i,
                "state":         "pending",
                "path":          None,
                "download_id":   None,
                "expected_file": None,
            }
            for i, m in enumerate(mods)
        ]
        self._active_downloads = 0
        self._install_head     = 0
        self._installing       = False

    def start(self):
        if not getattr(self, "_callbacks_connected", False):
            self.__plugin.organizer.downloadManager().onDownloadComplete(self._on_download_complete)
            self.__plugin.organizer.downloadManager().onDownloadFailed(self._on_download_failed)
            self.__plugin.organizer.downloadManager().onDownloadPaused(self._on_download_paused)
            self.__plugin.organizer.downloadManager().onDownloadRemoved(self._on_download_removed)
            self.__plugin.organizer.modList().onModInstalled(self._on_mod_installed)
            self._callbacks_connected = True
        QTimer.singleShot(0, self._pump)

    def _pump(self):
        for entry in self._mods:
            if entry["state"] != "pending":
                continue
            if self._active_downloads >= self.__plugin.concurrent_downloads:
                break
            self._begin(entry)
        QTimer.singleShot(0, self._try_install)

    def _begin(self, entry: dict):
        mod = entry["mod"]
        src = mod.get("source", {})

        if self._is_installed(mod):
            logger.info(f"[✓] Already installed: {mod['name']}")
            entry["state"] = "done"
            return

        if mod.get("optional", False):
            if not ask_yes_no(
                "Optional mod",
                f"Download {mod['name']} v{mod.get('version', '')}?\n\n"
                f"{mod.get('instructions', '')}",
            ):
                logger.info(f"[−] Skipping optional: {mod['name']}")
                entry["state"] = "done"
                return

        if src.get("type") != "nexus":
            logger.warning(f"[!] Unsupported source type '{src.get('type')}': {mod['name']}")
            entry["state"] = "failed"
            return

        mod_id  = int(src.get("modId",  0))
        file_id = int(src.get("fileId", 0))
        if not mod_id or not file_id:
            logger.warning(f"[!] Missing modId/fileId: {mod['name']}")
            entry["state"] = "failed"
            return

        path = self._is_downloaded(mod)
        if path:
            logger.info(f"[✓] Already downloaded: {mod['name']} ({os.path.basename(path)})")
            entry["state"] = "ready"
            entry["path"]  = path
            return

        entry["state"] = "downloading"
        self._active_downloads += 1

        try:
            if self.__plugin.nexus.is_premium:
                dl_id = self.__plugin.organizer.downloadManager() \
                            .startDownloadNexusFile(mod_id, file_id)
            else:
                game_id = self.__plugin.organizer.managedGame().nexusGameID()
                url     = self.get_download_url(file_id, game_id)
                if not url:
                    raise RuntimeError("No download URL returned")
                dl_id = self.__plugin.organizer.downloadManager() \
                            .startDownloadURLs([url])
        except Exception as e:
            logger.error(f"[!] Failed to start download for {mod['name']}: {e}")
            entry["state"] = "failed"
            self._active_downloads -= 1
            return

        entry["download_id"] = dl_id
        logger.info(f"[↓] Downloading [{entry['index']}] {mod['name']}  (dl_id={dl_id})")

    def _on_download_paused(self, download_id: int):
        path = self.__plugin.organizer.downloadManager().downloadPath(download_id)
        if not path:
            return
        basename = os.path.basename(path)
        entry = next(
            (e for e in self._mods
             if e["state"] == "downloading" and self._path_matches(e["mod"], basename)),
            None,
        )
        if entry is None:
            return
        self._active_downloads = max(0, self._active_downloads - 1)
        entry["state"] = "paused"
        logger.info(f"[~] Download paused [{entry['index']}] {entry['mod']['name']}")
        QTimer.singleShot(0, self._pump)

    def _on_download_failed(self, download_id: int):
        path = self.__plugin.organizer.downloadManager().downloadPath(download_id)
        if not path or not os.path.exists(path):
            return

        basename = os.path.basename(path)

        entry = next(
            (e for e in self._mods
             if e["state"] == "downloading"
             and self._path_matches(e["mod"], basename)),
            None,
        )
        if entry is None:
            return

        entry["state"] = "failed"

    def _on_download_removed(self, download_id: int):
        path = self.__plugin.organizer.downloadManager().downloadPath(download_id)
        if not path or not os.path.exists(path):
            return

        basename = os.path.basename(path)

        entry = next(
            (e for e in self._mods
             if e["state"] == "downloading"
             and self._path_matches(e["mod"], basename)),
            None,
        )
        if entry is None:
            return

        entry["state"] = "failed"

    def _on_download_complete(self, download_id: int):
        path = self.__plugin.organizer.downloadManager().downloadPath(download_id)
        if not path or not os.path.exists(path):
            return

        basename = os.path.basename(path)

        entry = next(
            (e for e in self._mods
             if e["state"] in ("downloading", "paused")
             and self._path_matches(e["mod"], basename)),
            None,
        )
        if entry is None:
            return

        self._active_downloads = max(0, self._active_downloads - 1)
        entry["state"] = "ready"
        entry["path"] = path
        logger.info(f"[✓] Download done [{entry['index']}] {entry['mod']['name']}")
        QTimer.singleShot(0, self._pump)

    def _try_install(self):
        if self._installing:
            return

        while self._install_head < len(self._mods):
            entry = self._mods[self._install_head]
            state = entry["state"]
            if state in ("done", "failed"):
                self._install_head += 1
                continue

            if state == "ready":
                self._install_head += 1
                self._installing = True
                self._do_install(entry)
                return

            break

        self._check_complete()

    def _do_install(self, entry: dict):
        if entry["state"] != "ready":
            logger.warning(
                f"[!] _do_install skipped [{entry['index']}] {entry['mod']['name']}: state={entry['state']!r}")
            self._installing = False
            QTimer.singleShot(0, self._try_install)
            return

        mod = entry["mod"]
        path = entry["path"]

        try:
            choices = mod.get("choices")
            if choices is not None:
                ctype = choices.get("type")
                if ctype == "fomod":
                    self.fomod_preprocessor(path, choices)
                else:
                    logger.warning(f"[!] unsupported choices type {ctype!r} for {mod['name']} - installing anyway")

            entry["state"] = "installing"
            entry["expected_file"] = os.path.basename(path)
            logger.info(f"[+] Installing [{entry['index']}] {mod['name']}")
            if self.__plugin.auto_install:
                QTimer.singleShot(0, lambda: self._auto_confirm_install_dialog(entry["mod"]))
            installed = self.__plugin.organizer.installMod(path)
            if mod.get("version") is not None:
                version = mobase.VersionInfo()
                version.parse(mod.get("version"))
                installed.setVersion(version)

        except Exception as e:
            logger.error(f"[!] _do_install failed for {mod['name']}: {e}")
            entry["state"] = "failed"
            self._installing = False
            QTimer.singleShot(0, self._try_install)

    def _on_mod_installed(self, mod_iface: "mobase.IModInterface"):
        install_file = os.path.basename(mod_iface.installationFile() or "")
        entry = next(
            (e for e in self._mods
             if e["state"] == "installing" and e.get("expected_file") == install_file),
            None,
        )
        if entry is None:
            return

        entry["state"] = "done"
        entry.pop("expected_file", None)
        logger.info(f"[✓] Installed [{entry['index']}] {mod_iface.name()}")

        self._installing = False
        QTimer.singleShot(0, self._try_install)

    def _check_complete(self):
        in_flight = {"pending", "paused", "downloading", "ready", "installing"}
        if not any(e["state"] in in_flight for e in self._mods):
            self._finish()

    def _finish(self):
        done   = sum(1 for e in self._mods if e["state"] == "done")
        failed = sum(1 for e in self._mods if e["state"] == "failed")
        logger.info(f"[+] Complete - {done} installed, {failed} failed / skipped")
        self.reorder_mods()
        if self._on_finish:
            self._on_finish()

    def _is_installed(self, mod: dict) -> bool:
        mod_id = mod.get("source", {}).get("modId")
        if not mod_id:
            return False
        needle = f"-{mod_id}-"
        for name in self.__plugin.organizer.modList().allMods():
            iface        = self.__plugin.organizer.modList().getMod(name)
            install_file = os.path.basename(iface.installationFile() or "")
            if needle in install_file:
                return True
        return False

    _ARCHIVE_EXTS = ("zip", "7z", "rar", "7zip", "tar")

    def _path_matches(self, mod: dict, basename: str) -> bool:
        import fnmatch
        src = mod.get("source", {})
        name = src.get("logicalFilename", "")
        mod_id = src.get("modId", "")
        version = mod.get("version", "").replace(".", "-")
        if not mod_id:
            return False

        if name:
            for ext in self._ARCHIVE_EXTS:
                if fnmatch.fnmatch(basename, f"{name}-{mod_id}-{version}-*.{ext}"):
                    return True
                if fnmatch.fnmatch(basename, f"{name}-{mod_id}-*.{ext}"):
                    return True

        needle = f"-{mod_id}-"
        return needle in basename

    def _is_downloaded(self, mod: dict) -> str | None:
        src = mod.get("source", {})
        name = src.get("logicalFilename", "")
        mod_id = src.get("modId", "")
        version = mod.get("version", "").replace(".", "-")
        if not mod_id:
            return None
        dl_path = self.__plugin.organizer.downloadsPath()

        if name:
            for ext in self._ARCHIVE_EXTS:
                matches = glob.glob(os.path.join(dl_path, f"{name}-{mod_id}-{version}-*.{ext}"))
                if matches:
                    return matches[0]

        needle = f"-{mod_id}-"
        for ext in self._ARCHIVE_EXTS:
            for f in glob.glob(os.path.join(dl_path, f"*{needle}*.{ext}")):
                return f
        return None

    def _auto_confirm_install_dialog(self, mod):
        for top in QApplication.topLevelWidgets():
            if top.objectName() == "SimpleInstallDialog":
                ok_btn = top.findChild(QPushButton, "okBtn")
                if ok_btn and ok_btn.isEnabled() and ok_btn.isVisible():
                    logger.info(f"[+] Auto-confirming SimpleInstallDialog")
                    ok_btn.click()
                    return
            if top.objectName() == "FomodInstallerDialog":
                if (mod.get("choices") is None or len(mod["choices"]) == 0) and not self.__plugin.auto_install_fomod:
                    return
                next_btn = top.findChild(QPushButton, "nextBtn")
                if next_btn and next_btn.isEnabled() and next_btn.isVisible():
                    while next_btn.text() != "Install":
                        if next_btn and next_btn.isEnabled() and next_btn.isVisible():
                            logger.info(f"[+] Auto-confirming FomodInstallerDialog")
                            next_btn.click()
                    next_btn.click()
                    return
        QTimer.singleShot(100, lambda: self._auto_confirm_install_dialog(mod))

    def _find_fomod_config(self, extract_path: str) -> str | None:
        for entry in os.scandir(extract_path):
            if entry.is_dir() and entry.name.lower() == "fomod":
                for f in os.scandir(entry.path):
                    if f.is_file() and f.name.lower() == "moduleconfig.xml":
                        return f.path
        return None
    
    def fomod_preprocessor(self, mod_path, choices):
        extract_path = os.path.join(self.__plugin.organizer.downloadsPath(), "extracted")
        extract_all(mod_path, extract_path)

        # added because some mods dont use lowercase `fomod` and instead whatever they want like `FOMod`
        xml_path = self._find_fomod_config(extract_path)
        
        if xml_path is None:
            logger.warning(f"[!] No ModuleConfig.xml found in {extract_path} - skipping fomod preprocessing")
            shutil.rmtree(extract_path)
            return
        
        parser   = etree.XMLParser(remove_blank_text=True)
        tree     = etree.parse(xml_path, parser)
        root     = tree.getroot()

        install_steps = root.find("installSteps")
        if install_steps is None:
            logger.info("No <installSteps> found.")
            return

        step_elements = install_steps.findall("installStep")
        options       = choices.get("options", [])
        modified      = False

        for step_idx, option in enumerate(options):
            if step_idx >= len(step_elements):
                break
            step       = step_elements[step_idx]
            group_list = step.find("optionalFileGroups")
            if group_list is None:
                continue
            group_elements = group_list.findall("group")
            for group_idx, group_choice in enumerate(option.get("groups", [])):
                if group_idx >= len(group_elements):
                    break
                group            = group_elements[group_idx]
                plugin_container = group.find("plugins")
                if plugin_container is None:
                    continue
                all_plugins    = plugin_container.findall("plugin")
                selected_names = {c["name"] for c in group_choice.get("choices", [])}
                new_plugins    = [p for p in all_plugins if p.get("name") in selected_names]
                if len(new_plugins) != len(all_plugins):
                    plugin_container[:] = new_plugins
                    modified = True

        if modified:
            tree.write(xml_path, encoding="utf-8", xml_declaration=True, pretty_print=True)
            write_all(extract_path, mod_path)

        shutil.rmtree(extract_path)

    def reorder_mods(self):
        mod_list = self.__plugin.organizer.modList()
        plugin_list = self.__plugin.organizer.pluginList()
        mod_list.setActive(
            mod_list.allMods(), True
        )
        logger.info("[+] Mod list activated")
        if len(self.load_order) > 0:
            for rule in self.load_order:
                rule_type = rule.get("type")
                if rule_type not in ("after", "before"):
                    continue
                src_mod = self._find_mod_by_rule_source(rule["source"])
                ref_mod = self._find_mod_by_rule_source(rule["reference"])
                if not src_mod or not ref_mod:
                    continue
                src_prio = mod_list.priority(src_mod.name())
                ref_prio = mod_list.priority(ref_mod.name())
                if rule_type == "after" and src_prio < ref_prio:
                    mod_list.setPriority(src_mod.name(), ref_prio + 1)
                elif rule_type == "before" and src_prio > ref_prio:
                    mod_list.setPriority(src_mod.name(), ref_prio)
            logger.info("[+] Mod list reordered to priority")

        if len(self.plugins_state) > 0:
            for state in self.plugins_state:
                name = state.get("name")
                enabled = state.get("enabled", False)
                if enabled:
                    plugin_list.setState(name, mobase.PluginState.ACTIVE)
                else:
                    plugin_list.setState(name, mobase.PluginState.INACTIVE)

            logger.info("[+] Plugin list states changed")

        if self.plugin_rules is not None and len(self.plugin_rules.get("plugins")) > 0:
            self.apply_plugin_loadorder(self.plugin_rules.get("plugins"))

        #TODO need to handle self.plugin_rules.get("groupds")

    def apply_plugin_loadorder(self, plugin_rules: list):
        plugin_list = self.__plugin.organizer.pluginList()
        all_plugins = list(plugin_list.pluginNames())
        known = {p.lower(): p for p in all_plugins}

        must_come_after: dict[str, set[str]] = {}
        for rule in plugin_rules:
            name = rule.get("name", "").lower()
            real_name = known.get(name)
            if not real_name:
                continue
            for dep in rule.get("after", []):
                real_dep = known.get(dep.lower())
                if not real_dep:
                    continue
                must_come_after.setdefault(real_name, set()).add(real_dep)

        if not must_come_after:
            return

        current_order = sorted(all_plugins, key=lambda p: plugin_list.priority(p))

        all_constrained = set(must_come_after.keys()) | {
            dep for deps in must_come_after.values() for dep in deps
        }

        constrained_current = [p for p in current_order if p in all_constrained]

        in_degree = {p: 0 for p in constrained_current}
        dependents: dict[str, list[str]] = {p: [] for p in constrained_current}

        for plugin, deps in must_come_after.items():
            if plugin not in in_degree:
                continue
            for dep in deps:
                if dep not in in_degree:
                    continue
                in_degree[plugin] += 1
                dependents[dep].append(plugin)

        queue = [p for p in constrained_current if in_degree[p] == 0]
        sorted_constrained = []
        while queue:
            node = queue.pop(0)
            sorted_constrained.append(node)
            for dependent in dependents.get(node, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(sorted_constrained) != len(constrained_current):
            logger.warning("[!] Plugin rule cycle detected - partial order applied")
            sorted_constrained += [p for p in constrained_current
                                   if p not in sorted_constrained]

        constrained_set = set(constrained_current)
        result: list[str] = []
        sort_iter = iter(sorted_constrained)
        for p in current_order:
            if p in constrained_set:
                result.append(next(sort_iter))
            else:
                result.append(p)

        logger.info(f"[+] Applying plugin load order rules ({len(must_come_after)} constraints)")
        plugin_list.setLoadOrder(result)

    def _find_mod_by_rule_source(self, source: dict):
        mod_list = self.__plugin.organizer.modList()
        file_expr = source.get("fileExpression", "")
        logical = source.get("logicalFileName", "")

        mod_id = None
        if file_expr:
            parts = file_expr.rsplit("-", 3)
            if len(parts) == 4:
                try:
                    mod_id = int(parts[1])
                except ValueError:
                    pass

        for mod_name in mod_list.allMods():
            mod = mod_list.getMod(mod_name)
            install_file = os.path.basename(mod.installationFile() or "")
            if not install_file:
                continue

            for ext in ("zip", "7z", "rar", "7zip", "tar"):
                if install_file == f"{file_expr}.{ext}":
                    return mod

            if mod_id and f"-{mod_id}-" in install_file:
                return mod

            if logical and install_file.startswith(logical + "-"):
                return mod

        return None

    def _get_last_real_mod_priority(self) -> int:
        mod_list = self.__plugin.organizer.modList()
        last_priority = 0
        for mod_name in mod_list.allMods():
            mod = mod_list.getMod(mod_name)
            if not mod.isForeign():
                continue
            prio = mod_list.priority(mod_name)
            if prio > last_priority:
                last_priority = prio
        return last_priority

    def get_session(self) -> str | None:
        if self._session is not None and self._session_expiry is not None:
            if time.time() + 30 < self._session_expiry:
                return self._session

        dump_path = os.path.join(DATA_PATH, "cookies")
        if not os.listdir(dump_path):
            subprocess.run([
                PYTHON_ENV.get("pythonw"),
                os.path.abspath(os.path.join(os.path.dirname(__file__), "v20.py")),
                os.path.abspath(dump_path),
                ".nexusmods.com:nexusmods_session",
                ".nexusmods.com:nexusmods_session_refresh",
            ])

        target_domain  = ".nexusmods.com"
        session_token  = None
        session_expiry = None
        refresh_token  = None

        for browser in BROWSERS:
            browser_path = os.path.join(dump_path, f"{browser}_cookies.json")
            if not os.path.isfile(browser_path):
                continue
            with open(browser_path) as f:
                # noinspection PyBroadException
                try:
                    data = json.load(f)
                except Exception:
                    continue
            for cookie in data:
                if cookie.get("domain") != target_domain:
                    continue
                name = cookie.get("name")
                if name == "nexusmods_session" and session_token is None:
                    session_token  = cookie["value"]
                    session_expiry = cookie.get("expires") or cookie.get("expirationDate")
                elif name == "nexusmods_session_refresh" and refresh_token is None:
                    refresh_token = cookie["value"]
            if session_token:
                break

        if session_token is None:
            return None

        if session_expiry is not None and time.time() >= session_expiry:
            session_token, session_expiry = None, None

        self._session        = session_token
        self._session_expiry = session_expiry
        return self._session

    def get_download_url(self, file_id: int, game_id: int) -> str | None:
        url = (
            f"https://www.nexusmods.com/Core/Libs/Common/Managers/Downloads"
            f"?GenerateDownloadUrl&fid={file_id}&game_id={game_id}"
        )
        try:
            session = self.get_session()
            if session is None:
                logger.warning("[!] No session token - cannot bypass freemium gate")
                return None
            response = self.__plugin.nexus.session.get(
                url, cookies={"nexusmods_session": session}
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                return data[0]["url"] if data else None
            return data["url"]
        except (RequestException, KeyError, IndexError, ValueError) as e:
            logger.warning(f"[!] get_download_url failed: {type(e).__name__}: {e}")
            return None
