import logging
import datetime
from pymongo import MongoClient
from bson.json_util import dumps, loads
from apex.database.StoragePluginBase import StoragePluginBase


class MongoDBPlugin(StoragePluginBase):
    def __init__(
        self,
        name: str,
        database_name: str,
        collection_name: str,
        host: str = 'localhost',
        port: int = 27017
    ):
        super().__init__(name)
        self.client = MongoClient(host, port)
        self.db = self.client[database_name]
        self.collection = self.db[collection_name]

    def sync(self, data: dict, id_field: str):
        """synchronize dict data to MongoDB"""
        logging.info(msg=f'synchronize data into MongoDB {self.collection}')
        if self.collection.count_documents({'_id': id_field}, limit=1) != 0:
            logging.info(msg=f'synchronizing with exist dataset (_id: {id_field})')
            self.collection.update_one({'_id': id_field}, {"$set": data})
        else:
            logging.info(msg=f'creating new dataset (_id: {id_field})')
            self.collection.insert_one(data)

    def record(self, data: dict, id_field: str):
        """record dict data to MongoDB"""
        logging.info(msg=f'synchronize data into MongoDB {self.collection}')
        # get timestamp
        timestamp = datetime.datetime.now().isoformat()
        _id = f'[{timestamp}]:{id_field}'
        logging.info(msg=f'creating new dataset (_id: {_id})')
        data['_id'] = _id
        self.collection.insert_one(data)

    def load_json(self, query):
        """load BSON from MongoDB"""
        cursor = self.collection.find(query)
        return [loads(dumps(doc)) for doc in cursor]

    def close(self):
        self.client.close()