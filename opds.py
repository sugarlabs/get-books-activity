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


import feedparser
import threading

import gobject, gtk

_FEEDBOOKS_URI = 'http://feedbooks.com/books/search.atom?query='
_INTERNETARCHIVE_URI = 'http://bookserver.archive.org/catalog/opensearch?q='

_REL_OPDS_ACQUISTION = u'http://opds-spec.org/acquisition'


class DownloadThread(threading.Thread):
    def __init__(self, obj):
        threading.Thread.__init__ (self)
        self.obj = obj
        self.stopthread = threading.Event()

    def _download(self):
        feedobj = feedparser.parse(self.obj._uri + self.obj._queryterm.replace(' ', '+'))
        self.obj._feedobj = feedobj

        gtk.gdk.threads_enter()
        self.obj.emit('completed')
        gtk.gdk.threads_leave()
        
        return False
    
    def run (self):
        self._download()
      
    def stop(self):
        self.stopthread.set()


class Book(object):
    def __init__(self, entry):
        self._entry = entry

    def get_title(self):
        return self._entry['title']

    def get_author(self):
        return self._entry['author']

    def get_download_links(self):
        ret = {}
        for link in self._entry['links']:
            if link['rel'] == _REL_OPDS_ACQUISTION:
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


class QueryResult(gobject.GObject):
    __gsignals__ = {
        'completed': (gobject.SIGNAL_RUN_FIRST,
                          gobject.TYPE_NONE,
                          ([])),
    }
    def __init__(self, uri, queryterm):
        gobject.GObject.__init__(self)

        self._uri = uri
        self._queryterm = queryterm
        self._feedobj = None

        self.threads = []
        
        d_thread = DownloadThread(self)
        self.threads.append(d_thread)
        d_thread.start()

    def __len__(self):
        return len(self._feedobj['entries'])

    def cancel(self):
        '''
        Cancels the query job
        '''                
        for d_thread in self.threads:
            d_thread.stop()


    def get_book_n(self, n):
        return Book(self._feedobj['entries'][n])

    def get_book_list(self):
        ret = []
        for entry in self._feedobj['entries']:
            ret.append(Book(entry))

        return ret

class FeedBooksQueryResult(QueryResult):
    def __init__(self, queryterm):
        QueryResult.__init__(self, _FEEDBOOKS_URI, queryterm)

class InternetArchiveQueryResult(QueryResult):
    def __init__(self, queryterm):
        QueryResult.__init__(self, _INTERNETARCHIVE_URI, queryterm)
