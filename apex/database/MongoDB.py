import logging
import datetime
from pymongo import MongoClient
from bson.json_util import dumps, loads
from apex.database.StorageBase import StorageBase
from apex.utils import update_dict


class MongoDBClient(StorageBase):
    def __init__(
        self,
        name: str,
        database_name: str,
        collection_name: str,
        **kwargs
    ):
        super().__init__(name)
        self.client = MongoClient(**kwargs)
        # Send a ping to confirm a successful connection
        try:
            self.client.admin.command('ping')
            logging.info(msg="Successfully connected to MongoDB!")
        except Exception as e:
            raise e
        self.db = self.client[database_name]
        self.collection = self.db[collection_name]

    def sync(self, data: dict, id_field: str, depth: int = 9999):
        """synchronize dict data to MongoDB"""
        logging.info(msg=f'Synchronize data into MongoDB {self.collection}')
        if self.collection.count_documents({'_id': id_field}, limit=1) != 0:
            logging.info(msg=f'Synchronizing with exist dataset (_id: {id_field})')
            orig_dict = self.collection.find_one({'_id': id_field})
            update_dict(orig_dict, data, depth)
            self.collection.update_one({'_id': id_field}, {"$set": orig_dict})
        else:
            logging.info(msg=f'Creating new dataset... (_id: {id_field})')
            self.collection.insert_one(data)

    def record(self, data: dict, id_field: str):
        """record dict data to MongoDB"""
        logging.info(msg=f'Record data into MongoDB {self.collection}')
        # get timestamp
        timestamp = datetime.datetime.now().isoformat()
        _id = f'[{timestamp}]:{id_field}'
        logging.info(msg=f'Creating new dataset... (_id: {_id})')
        data['_id'] = _id
        self.collection.insert_one(data)

    def load_json(self, query):
        """load BSON from MongoDB"""
        cursor = self.collection.find(query)
        return [loads(dumps(doc)) for doc in cursor]

    def close(self):
        self.client.close()
