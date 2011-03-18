#! /usr/bin/env python

# Copyright (C) 2009 Sayamindu Dasgupta <sayamindu@laptop.org>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

import logging

import feedparser
import threading
import os

import gobject
import gtk

_logger = logging.getLogger('get-ia-books-activity')

_FEEDBOOKS_URI = 'http://feedbooks.com/books/search.atom?query='
_INTERNETARCHIVE_URI = 'http://bookserver.archive.org/catalog/opensearch?q='

_REL_OPDS_ACQUISTION = u'http://opds-spec.org/acquisition'
_REL_OPDS_COVER = u'http://opds-spec.org/image'

gobject.threads_init()


class DownloadThread(threading.Thread):

    def __init__(self, obj, midway):
        threading.Thread.__init__(self)
        self.midway = midway
        self.obj = obj
        self.stopthread = threading.Event()

    def _download(self):
        if self.obj._win is not None:
            self.obj._win.set_cursor(gtk.gdk.Cursor(gtk.gdk.WATCH))
        if not self.obj.is_local() and self.midway == False:
            feedobj = feedparser.parse(self.obj._uri + \
                    self.obj._queryterm.replace(' ', '+'))
        else:
            feedobj = feedparser.parse(self.obj._uri)

        for entry in feedobj['entries']:
            self.obj._booklist.append(Book(entry))
        self.obj._feedobj = feedobj
        self.obj.emit('updated', self.midway)
        self.obj._ready = True
        if self.obj._win is not None:
            self.obj._win.set_cursor(None)
        return False

    def run(self):
        self._download()

    def stop(self):
        self.stopthread.set()


class Book(object):

    def __init__(self, entry, basepath=None):
        self._entry = entry
        self._basepath = basepath

    def get_title(self):
        try:
            ret = self._entry['title']
        except KeyError:
            ret = 'Unknown'

        return ret

    def get_author(self):
        try:
            ret = self._entry['author']
        except KeyError:
            ret = 'Unknown'

        return ret

    def get_download_links(self):
        ret = {}
        for link in self._entry['links']:
            if link['rel'] == _REL_OPDS_ACQUISTION:
                if self._basepath is not None and \
                        not (link['href'].startswith('http') or \
                                link['href'].startswith('ftp')):
                    ret[link['type']] = 'file://' \
                        + os.path.join(self._basepath, link['href'])
                else:
                    ret[link['type']] = link['href']

        return ret

    def get_publisher(self):
        try:
            ret = self._entry['dcterms_publisher']
        except KeyError:
            ret = 'Unknown'

        return ret

    def get_published_year(self):
        try:
            ret = self._entry['published']
        except KeyError:
            ret = 'Unknown'

        return ret

    def get_language(self):
        try:
            ret = self._entry['dcterms_language']
        except KeyError:
            ret = 'Unknown'

        return ret

    def get_image_url(self):
        try:
            ret = {}
            for link in self._entry['links']:
                if link['rel'] == _REL_OPDS_COVER:
                    if self._basepath is not None and \
                            not (link['href'].startswith('http') or \
                                    link['href'].startswith('ftp')):
                        ret[link['type']] = 'file://' \
                            + os.path.join(self._basepath, link['href'])
                    else:
                        ret[link['type']] = link['href']
        except KeyError:
            ret = 'Unknown'
        return ret

    def match(self, terms):
        #TODO: Make this more comprehensive
        for term in terms.split('+'):
            if term in self.get_title():
                return True
            if term in self.get_author():
                return True
            if term in self.get_publisher():
                return True
        return False


class QueryResult(gobject.GObject):

    __gsignals__ = {
        'updated': (gobject.SIGNAL_RUN_FIRST,
                          gobject.TYPE_NONE,
                          ([gobject.TYPE_BOOLEAN])),
    }

    def __init__(self, uri, queryterm, win):
        gobject.GObject.__init__(self)
        self._uri = uri
        self._queryterm = queryterm
        self._win = win
        self._feedobj = None
        self._next_uri = ''
        self._ready = False
        self._booklist = []
        self.threads = []
        self._start_download()

    def _start_download(self, midway=False):
        d_thread = DownloadThread(self, midway)
        self.threads.append(d_thread)
        d_thread.start()

    def __len__(self):
        return len(self._booklist)

    def has_next(self):
        '''
        Returns True if more result pages are
        available for the resultset
        '''
        if not 'links' in self._feedobj['feed']:
            return False
        for link in self._feedobj['feed']['links']:
            if link['rel'] == u'next':
                self._next_uri = link['href']
                return True

        return False

    def update_with_next(self):
        '''
        Updates the booklist with the next resultset
        '''
        if len(self._next_uri) > 0:
            self._ready = False
            self._uri = self._next_uri
            self.cancel()  # XXX: Is this needed ?
            self._start_download(midway=True)

    def cancel(self):
        '''
        Cancels the query job
        '''
        for d_thread in self.threads:
            d_thread.stop()

    def get_book_n(self, n):
        '''
        Gets the n-th book
        '''
        return self._booklist[n]

    def get_book_list(self):
        '''
        Gets the entire booklist
        '''
        return self._booklist

    def is_ready(self):
        '''
        Returns False if a query is in progress
        '''
        return self._ready

    def is_local(self):
        '''
        Returns True in case of a local school
        server or a local device
        (yay! for sneakernet)
        '''
        return False


class LocalVolumeQueryResult(QueryResult):

    def __init__(self, path, queryterm, win):
        QueryResult.__init__(self, os.path.join(path, 'catalog.xml'),
                queryterm, win)

    def is_local(self):
        return True

    def get_book_list(self):
        ret = []
        if self._queryterm is None or self._queryterm is '':
            for entry in self._feedobj['entries']:
                ret.append(Book(entry, basepath=os.path.dirname(self._uri)))
        else:
            for entry in self._feedobj['entries']:
                book = Book(entry, basepath=os.path.dirname(self._uri))
                if book.match(self._queryterm.replace(' ', '+')):
                    ret.append(book)
        return ret


class FeedBooksQueryResult(QueryResult):
    def __init__(self, queryterm, win):
        QueryResult.__init__(self, _FEEDBOOKS_URI, queryterm, win)


class InternetArchiveQueryResult(QueryResult):
    def __init__(self, queryterm, win):
        QueryResult.__init__(self, _INTERNETARCHIVE_URI, queryterm, win)
