import boto3
import datetime
import logging
import time
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from apex.database.StorageBase import StorageBase
from apex.utils import update_dict, convert_floats_to_decimals


class DynamoDBClient(StorageBase):
    def __init__(self, name: str, table_name: str, **kwargs):
        super().__init__(name)
        kwargs["service_name"] = 'dynamodb'
        self.dynamodb = boto3.resource(**kwargs)
        try:
            self.dynamodb.meta.client.describe_table(TableName=table_name)
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
                # The table doesn't exist, we create it
                logging.info(msg=f'DynamoDB table (name: {table_name}) not exist, will create it')
                self.dynamodb.create_table(
                    TableName=table_name,
                    KeySchema=[{'AttributeName': 'id', 'KeyType': 'HASH'}],
                    AttributeDefinitions=[{'AttributeName': 'id', 'AttributeType': 'S'}],
                    ProvisionedThroughput={'ReadCapacityUnits': 5, 'WriteCapacityUnits': 5}
                )
                # Wait until the table exists.
                while True:
                    logging.info(msg=f'Waiting for table to be created...')
                    response = self.dynamodb.meta.client.describe_table(TableName=table_name)
                    status = response['Table']['TableStatus']
                    if status == 'ACTIVE':
                        logging.info(msg=f'Table creation is successful (name: {table_name})')
                        break
                    time.sleep(2)  # Wait for 5 seconds before checking again
            else:
                raise  # re-raise the exception if it's not because the table doesn't exist
        self.table = self.dynamodb.Table(table_name)

    def sync(self, data: dict, id_field: str, depth: int = 9999):
        """synchronize dict data to DynamoDB"""
        data = convert_floats_to_decimals(data)
        logging.info(msg=f'Synchronize data into DynamoDB {self.table}')
        response = self.table.get_item(Key={'id': id_field})
        orig_dict = response.get('Item', None)
        if orig_dict is not None:
            logging.info(msg=f'Synchronize data with exist dataset (id: {id_field})')
            update_dict(orig_dict, data, depth)
            self.table.put_item(Item=orig_dict)
        else:
            logging.info(msg=f'Creating new dataset... (id: {id_field})')
            data['id'] = id_field
            self.table.put_item(Item=data)

    def record(self, data: dict, id_field: str):
        """record dict data to DynamoDB"""
        data = convert_floats_to_decimals(data)
        logging.info(msg=f'Record data into DynamoDB {self.table}')
        # get timestamp
        timestamp = datetime.datetime.now().isoformat()
        the_id = f'[{timestamp}]:{id_field}'
        logging.info(msg=f'Creating new dataset... (id: {the_id})')
        data['id'] = the_id
        self.table.put_item(Item=data)

    def load_json(self, query):
        """load JSON from DynamoDB"""
        response = self.table.scan(FilterExpression=Key('id').eq(query))
        return response['Items']

    def close(self):
        pass  # No close operation needed for DynamoDB with Boto3
