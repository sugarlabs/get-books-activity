import json
import logging

BOOKS_DATA_LOCAL = '../books.json'

all_books = []
with open(BOOKS_DATA_LOCAL) as local_cache:
    all_books = json.load(local_cache)

tags = []
subjects = []
for book_data in all_books:
    for tag in book_data['tags']:
        if tag not in tags:
            tags.append(tag)

    for subject in book_data['subjects']:
        if subject not in subjects:
            subjects.append(subject)

logging.error('TAGS %s', tags)
logging.error('SUBJECTS %s', subjects)

