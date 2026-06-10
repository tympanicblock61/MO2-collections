import json
import os
from urllib.parse import urlparse
import mobase

from nxmhandler.app.utils import CollectionUrl, NxmUrl
from nxmhandler.plugin import NXMHandler
from .mo2_natives import read_mo2_api_key
from .modinstaller import ModInstaller
from .utils import logger, extract_all, download_file, DATA_PATH
# importing utils also causes libs to be loaded to use these

# noinspection PyPackageRequirements
from curl_cffi import Session



# noinspection PyTypeChecker
class NexusCollections(NXMHandler):
    def __init__(self):
        super(NexusCollections, self).__init__(None, ["collections"])
        self._organizer: mobase.IOrganizer = None
        self.nexus: NexusHandler = None
        self.modInstaller: ModInstaller = None

    def init(self, organizer: mobase.IOrganizer):
        super().init(organizer)
        self._organizer = organizer
        self.nexus = NexusHandler(self._organizer, self)
        self.modInstaller = ModInstaller(self, [], [], [], {})

    def author(self) -> str:
        return "TympanicBlock61"

    def description(self) -> str:
        return "A Mo2 plugin that adds support for NexusMods Collections"

    def name(self):
        return "NexusCollections Manager"

    def settings(self):
        return [
            mobase.PluginSetting("nexus_api_key", "Your personal Nexus Mods API key", ""),
            mobase.PluginSetting("view_adult_content", "View adult content", True),
            mobase.PluginSetting("concurrent_downloads", "Number of concurrent downloads", 3),
            mobase.PluginSetting("auto_install", "Automate fomod and simple install dialogs (does not for manual)", True),
            mobase.PluginSetting("auto_install_fomod", "Automate install of fomod, even ones with options to select", False)
        ]

    @property
    def adult(self) -> bool:
        return self._organizer.pluginSetting(self.name(), "view_adult_content")

    @property
    def concurrent_downloads(self) -> int:
        return self._organizer.pluginSetting(self.name(), "concurrent_downloads")

    @property
    def auto_install(self):
        return self._organizer.pluginSetting(self.name(), "auto_install")

    @property
    def auto_install_fomod(self):
        return self._organizer.pluginSetting(self.name(), "auto_install_fomod")

    def version(self):
        return mobase.VersionInfo(1, 0, 0, 0)

    def get_revision(self, slug: str):
        data_ = self.nexus.get_collection(slug, self.adult, None)
        revisions = data_.get('data').get('collection').get('revisions')
        revision = -1
        for i in range(len(revisions)):
            if revisions[i].get("revisionStatus") == "published":
                revision = i
                break
        return revision

    def extract_collection(self, collection_zip: str):
        collection_data = {}

        extract_all(collection_zip, self._organizer.modsPath())

        for root, dirs, files in os.walk(self._organizer.modsPath(), topdown=False):
            if "collection.json" in files:
                collection_path = os.path.join(root, "collection.json")
                with open(collection_path, 'r', encoding='utf-8') as f:
                    try:
                        collection_data = json.load(f)
                        logger.info(f"[+] Parsed collection.json")
                    except json.JSONDecodeError:
                        logger.warning(f"[!] Invalid JSON in: {collection_path}")
                os.remove(collection_path)

            if not files and not dirs:
                try:
                    os.rmdir(root)
                    logger.info(f"[+] Deleted empty folder: {root}")
                except OSError:
                    logger.warning(f"[!] Failed to delete folder: {root}")

        return collection_data

    def nxm_receive_url(self, url: NxmUrl):
        if not isinstance(url, CollectionUrl):
            return

        if url.revision is None:
            url.revision = self.get_revision(url.slug)
            if url.revision == -1:
                logger.error(f"valid revision for {url.slug} cannot be found")
                return

        data_ = self.nexus.get_collection_revision(url.slug, url.revision, self.adult, None)
        collection_revision = data_.get('data').get('collection').get('currentRevision')
        download_link = self.nexus.session.get(f"{self.nexus.NEXUS_API}{collection_revision.get('downloadLink')}").json()
        uri = download_link.get('download_links')[0].get('URI')
        collection_zip = os.path.join(self._organizer.downloadsPath(), os.path.basename(urlparse(uri).path))
        download_file(uri, collection_zip)
        collection_json = self.extract_collection(collection_zip)
        logger.info(f"[+] Downloaded collection.json")
        self.modInstaller = ModInstaller(self, collection_json.get('mods'), collection_json.get("modRules"), collection_json.get("plugins"), collection_json.get("pluginRules"))
        self.modInstaller.start()

        print("does this execute")

    @property
    def organizer(self):
        return self._organizer

def load_graphql(query_path: str):
    with open(os.path.abspath(os.path.join(DATA_PATH, query_path)), 'r') as f:
        query = f.read()
        f.close()
        return query

# https://github.com/Nexus-Mods/Vortex/blob/00d256095b356a9afe86fef5b9ba70c78cb9e494/src/extensions/nexus_integration/util/graphQueries.ts#L67
# noinspection PyTypeChecker
class NexusHandler:
    NEXUS_API = "https://api.nexusmods.com"
    GRAPHQL_URL = f"{NEXUS_API}/v2/graphql"

    def __init__(self, organizer: mobase.IOrganizer, plugin: mobase.IPlugin,):
        self._organizer = organizer
        self.__plugin = plugin
        # noinspection PyArgumentList
        self.session = Session(impersonate="chrome")
        self.session.headers = {
            'apikey': None,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

        self.cached_validate: dict = None

    @property
    def is_premium(self) -> bool:
        if self.cached_validate is None: self.validate()
        return self.cached_validate.get("is_premium", False)

    #userid, is_premium, etc self.nexus.session.get("https://api.nexusmods.com/v1/users/validate.json")
    @property
    def api_key(self) -> str:
        try:
            key = read_mo2_api_key()
            if key:
                logger.info(f"[+] API key extracted from Credentials (len={len(key)})")
                return key
        except Exception as e:
            logger.warning("[!] API key extraction failed: "+str(e))

        key = self._organizer.pluginSetting(self.__plugin.name(), "nexus_api_key")
        if not key:
            logger.error(
                "[!] No API key available - set one in MO2 plugin settings "
                "or log into Nexus Mods in your browser so it can be extracted automatically"
            )
        return key or ""

    def _check_auth(self) -> bool:
        key = self.api_key
        if not key:
            logger.error("[!] Aborting request - no API key")
            return False
        self.session.headers.update({"apikey": key})
        return True

    def validate(self):
        self.cached_validate = self.session.get(f"{self.NEXUS_API}/v1/users/validate.json").json()

    def get_collection(self, slug: str, viewAdultContent: bool, domainName: str):
        payload = {
            'query': load_graphql("queries/collection.qry"),
            'variables': {
                'slug': slug,
                'viewAdultContent': viewAdultContent,
                'domainName': domainName
            }
        }

        self.session.headers.update({
            "apikey": self.api_key
        })

        return self.session.post(self.GRAPHQL_URL, json=payload).json()


    def get_collection_revision(self, slug: str, revision: int, viewAdultContent: bool, domainName: str):
        if not self._check_auth():
            return {}
        payload = {
            'query': load_graphql("queries/collection.qry"),
            'variables': {
                'slug': slug,
                'revision': revision,
                'viewAdultContent': viewAdultContent,
                'domainName': domainName
            }
        }

        self.session.headers.update({
            "apikey": self.api_key
        })

        return self.session.post(self.GRAPHQL_URL, json=payload).json()