import json
import logging

lang = 'HT'
BOOKS_DATA_LOCAL = './books.json'

all_books = []
with open(BOOKS_DATA_LOCAL) as local_cache:
    all_books = json.load(local_cache)

count = 0
for book_data in all_books:
    if lang in book_data['languages']:
        logging.error('%s (%s)', book_data['name'], book_data['_id'])
        count += 1

logging.error('TOTAL %d', count)
