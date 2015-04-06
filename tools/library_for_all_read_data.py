import urllib2
import json
import os
import sys
import logging

LIB_FOR_ALL_BASE_URL = 'https://haiti.libraryforall.org:6984/catalog/'
ALL_BOOKS_URL = LIB_FOR_ALL_BASE_URL + '_all_docs'

ALL_BOOKS_CATALOG_LOCAL = './all_books.json'
BOOKS_DATA_LOCAL = './books.json'

if os.path.exists(ALL_BOOKS_CATALOG_LOCAL):
    with open(ALL_BOOKS_CATALOG_LOCAL) as local_cache:
        all_books = json.load(local_cache)
else:
    req = urllib2.Request(ALL_BOOKS_URL, None)
    opener = urllib2.build_opener()
    f = opener.open(req)
    all_books_json = f.read()
    all_books = json.loads(all_books_json)
    # write the cache locally
    with open(ALL_BOOKS_CATALOG_LOCAL, 'w') as local_cache:
        json.dump(all_books, local_cache)

books_data = []

for row in all_books['rows']:
    # get the information for the book
    try:
        book_id = int(row['id'])
        logging.error('Reading book %s', book_id)
        # get the data from the specific book
        book_data_url = LIB_FOR_ALL_BASE_URL + str(book_id)
        logging.error('get %s', book_data_url)
        req = urllib2.Request(book_data_url, None)
        opener = urllib2.build_opener()
        f = opener.open(req)
        book_json = f.read()
        book_data = json.loads(book_json)
        books_data.append(book_data)
    except:
        logging.exception("Unexpected error: %s", sys.exc_info()[0])
        # some special ids are not int and are not books
        pass

# dump the complete books data to disk
with open(BOOKS_DATA_LOCAL, 'w') as local_cache:
    json.dump(books_data, local_cache)
