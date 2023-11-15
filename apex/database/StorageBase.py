from abc import ABC, abstractmethod


class StorageBase(ABC):
    def __init__(self, name):
        """
        Initiation of database

        Parameters
        ----------
        parameter : name
        """
        self.name = name

    @abstractmethod
    def sync(self, data: dict, id_field: str, depth: int):
        pass

    @abstractmethod
    def record(self, data: dict, id_field: str):
        pass

    @abstractmethod
    def load_json(self, query):
        pass

    @abstractmethod
    def close(self):
        pass
