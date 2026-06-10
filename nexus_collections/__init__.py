import mobase
from .NexusCollections import NexusCollections

def createPlugin() -> mobase.IPlugin:
    return NexusCollections()