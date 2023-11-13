class StoragePluginBase:
    def __init__(self, name):
        self.name = name

    def sync(self, data: dict, id_field: str):
        raise NotImplementedError

    def record(self, data: dict, id_field: str):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError
