class StoragePluginBase:
    def __init__(self, name):
        self.name = name

    def sync(self, data, id_field):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError
