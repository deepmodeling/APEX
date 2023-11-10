import logging

from pymongo import MongoClient
from bson.json_util import dumps, loads
from apex.plugins.PluginBase import StoragePluginBase


class MongoDBPlugin(StoragePluginBase):
    def __init__(
        self,
        name,
        database_name,
        collection_name,
        host='localhost',
        port=27017
    ):
        super().__init__(name)
        self.client = MongoClient(host, port)
        self.db = self.client[database_name]
        self.collection = self.db[collection_name]

    def sync(self, data, id_field):
        """update dict data to MongoDB"""
        logging.info(msg=f'storing data into MongoDB {self.collection}')
        if self.collection.count_documents({'_id': id_field}, limit=1) != 0:
            logging.info(msg=f'synchronizing with exist dataset (_id: {id_field})')
            self.collection.update_one({'_id': id_field}, {"$set": data})
        else:
            logging.info(msg=f'creating new dataset (_id: {id_field})')
            self.collection.insert_one(data)

    def load_json(self, query):
        """load BSON from MongoDB"""
        cursor = self.collection.find(query)
        return [loads(dumps(doc)) for doc in cursor]

    def close(self):
        self.client.close()
