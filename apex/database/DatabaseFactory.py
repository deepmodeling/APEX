from .DynamoDB import DynamoDBClient
from .MongoDB import MongoDBClient


class DatabaseFactory:
    @staticmethod
    def create_database(method, *args, **kwargs):
        if method == 'mongodb':
            return MongoDBClient(*args, **kwargs)
        elif method == 'dynamodb':
            return DynamoDBClient(*args, **kwargs)
        else:
            raise TypeError(f"Not supported database type: {method}")
