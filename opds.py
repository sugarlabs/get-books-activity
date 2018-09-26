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

from gi.repository import GLib
from gi.repository import GObject
from gi.repository import Gtk

from sugar3 import network

import logging
import threading
import os
import urllib
import time
import csv

import sys
sys.path.insert(0, './')
import feedparser

_REL_OPDS_ACQUISTION = u'http://opds-spec.org/acquisition'
_REL_SUBSECTION = 'subsection'
_REL_OPDS_POPULAR = u'http://opds-spec.org/sort/popular'
_REL_OPDS_NEW = u'http://opds-spec.org/sort/new'
_REL_ALTERNATE = 'alternate'
_REL_CRAWLABLE = 'http://opds-spec.org/crawlable'

GObject.threads_init()


class ReadURLDownloader(network.GlibURLDownloader):
    """URLDownloader that provides content-length and content-type."""

    def get_content_length(self):
        """Return the content-length of the download."""
        if self._info is not None:
            length = self._info.headers.get('Content-Length')
            if length is not None:
                return int(length)
            else:
                return 0

    def get_content_type(self):
        """Return the content-type of the download."""
        if self._info is not None:
            return self._info.headers.get('Content-type')
        return None


class DownloadThread(threading.Thread):

    def __init__(self, uri, headers, feedobj_cb):
        threading.Thread.__init__(self)
        self._uri = uri
        self._headers = headers
        self._feedobj_cb = feedobj_cb

        self.stopthread = threading.Event()

    def run(self):
        logging.error('Searching URL %s headers %s' % (self._uri,
                                                       self._headers))
        feedobj = feedparser.parse(self._uri, request_headers=self._headers)
        self._feedobj_cb(feedobj)

    def stop(self):
        self.stopthread.set()


class Book(object):

    def __init__(self, configuration, entry, basepath=None):
        self._entry = entry
        self._basepath = basepath
        self._configuration = configuration

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

    def get_types(self):
        ret = {}
        for link in self._entry['links']:
            if link['rel'].startswith(_REL_OPDS_ACQUISTION):
                if self._basepath is not None and \
                        not (link['href'].startswith('http') or \
                                link['href'].startswith('ftp')):
                    ret[link['type']] = 'file://' \
                        + os.path.join(self._basepath, link['href'])
                else:
                    ret[link['type']] = link['href']
            elif link['rel'] in \
            [_REL_OPDS_POPULAR, _REL_OPDS_NEW, _REL_SUBSECTION]:
                ret[link['type']] = link['href']
            elif link['rel'] == _REL_ALTERNATE:
                ret[link['type']] = link['href']
            else:
                pass
        return ret

    def get_download_links(self, content_type, download_cb, _):
        types = self.get_types()
        if content_type in types:
            url = types[content_type]
            GLib.idle_add(download_cb, url)

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
                if link['rel'] == self._configuration['opds_cover']:
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

    def get_summary(self):
        if self._configuration is not None \
            and 'summary_field' in self._configuration:
                try:
                    ret = self._entry[self._configuration['summary_field']]
                except KeyError:
                    ret = 'Unknown'
        else:
                ret = 'Unknown'
        return ret

    def get_object_id(self):
        try:
            ret = self._entry['object_id']
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


class QueryResult(GObject.GObject):

    __gsignals__ = {
        'updated': (GObject.SignalFlags.RUN_FIRST,
                          None,
                          ([bool])),
    }

    def __init__(self, configuration, query, language):
        GObject.GObject.__init__(self)
        self._configuration = configuration
        self._uri = self._configuration['query_uri']
        self._query = query
        self._language = language
        self._feedobj = None
        self._next_uri = ''
        self._ready = False
        self._booklist = []
        self._cataloglist = []
        self.threads = []

        uri = self._uri
        headers = {}
        if not self.is_local():
            uri += self._query.replace(' ', '+')
            if self._language is not None and self._language != 'all':
                headers['Accept-Language'] = self._language
                uri += '&lang=' + self._language

        d_thread = DownloadThread(uri, headers, self.__feedobj_cb)
        d_thread.daemon = True
        self.threads.append(d_thread)
        d_thread.start()

    def __feedobj_cb(self, feedobj):
        self._feedobj = feedobj

        # Get catalog Type
        CATALOG_TYPE = 'COMMON'
        if 'links' in feedobj['feed']:
            for link in feedobj['feed']['links']:
                if link['rel'] == _REL_CRAWLABLE:
                    CATALOG_TYPE = 'CRAWLABLE'
                    break

        def entry_type(entry):
            for link in entry['links']:
                if link['rel'] in \
                    [_REL_OPDS_POPULAR, _REL_OPDS_NEW, _REL_SUBSECTION]:
                    return "CATALOG"
                else:
                    return 'BOOK'

        for entry in feedobj['entries']:
            if entry_type(entry) == 'BOOK' and CATALOG_TYPE is not 'CRAWLABLE':
                self._booklist.append(Book(self._configuration, entry))
            elif entry_type(entry) == 'CATALOG' or CATALOG_TYPE == 'CRAWLABLE':
                self._cataloglist.append(Book(self._configuration, entry))

        self._ready = True
        self.emit('updated', False)

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

    def get_catalog_list(self):
        '''
        Gets the entire catalog list
        '''
        return self._cataloglist

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

    def __init__(self, path, query, language):
        configuration = {'query_uri': os.path.join(path, 'catalog.xml')}
        QueryResult.__init__(self, configuration, query, language)

    def is_local(self):
        return True

    def get_book_list(self):
        ret = []
        if self._query is None or self._query is '':
            for entry in self._feedobj['entries']:
                ret.append(Book(entry, basepath=os.path.dirname(self._uri)))
        else:
            for entry in self._feedobj['entries']:
                book = Book(entry, basepath=os.path.dirname(self._uri))
                if book.match(self._query.replace(' ', '+')):
                    ret.append(book)
        return ret


class RemoteQueryResult(QueryResult):

    def __init__(self, configuration, query, language):
        QueryResult.__init__(self, configuration, query, language)


class InternetArchiveBook(Book):

    def __init__(self, configuration, entry, basepath=None):
        Book.__init__(self, configuration, entry, basepath=None)

    def get_types(self):
        return self._entry['links']

    def get_download_links(self, content_type, download_cb, path):
        """
        Download and parse the {identifier}_files.xml file list in the
        {identifier} directory and choose a file matching the
        requested content type.
        """
        url_base = 'http://archive.org/download/%s' % self._entry['identifier']
        url = os.path.join(url_base, '%s_files.xml' % self._entry['identifier'])

        downloader = FileDownloader(url, path)

        def updated(downloader, path, _):
            if path is None:
                logging.error('internet archive file list get fail')
                # FIXME: report to user a failure to download
                return

            from xml.etree.ElementTree import XML
            xml = XML(open(path, 'r').read())
            os.remove(path)

            table = {
                'text pdf': u'application/pdf',
                'grayscale luratech pdf': u'application/pdf-bw',
                'image container pdf': u'application/pdf',
                'djvu': u'image/x.djvu',
                'epub': u'application/epub+zip',
            }

            chosen = None
            for element in xml.findall('file'):
                fmt = element.find('format').text.lower()
                if fmt in table:
                    if table[fmt] == content_type:
                        chosen = element.get('name')
                        break

            if chosen is None:
                logging.error('internet archive file list omits content type')
                # FIXME: report to user a failure to find matching content
                return

            url = os.path.join(url_base, chosen)
            GLib.idle_add(download_cb, url)

        downloader.connect('updated', updated)

    def get_image_url(self):
        return {'jpg': self._entry['cover_image']}


class InternetArchiveDownloadThread(threading.Thread):

    def __init__(self, query, path, updated_cb, append_cb, ready_cb):
        threading.Thread.__init__(self)
        self._path = path
        self._updated_cb = updated_cb
        self._append_cb = append_cb
        self._ready_cb = ready_cb

        self._download_content_length = 0
        self._download_content_type = None

        FL = urllib.quote('fl[]')
        SORT = urllib.quote('sort[]')
        self._url = 'http://archive.org/advancedsearch.php?q=' +  \
            urllib.quote('(title:(' + query.lower() + ') OR ' + \
            'creator:(' + query.lower() + ')) AND format:(DJVU)')
        self._url += '&' + FL + '=creator&' + FL + '=description&' + \
            FL + '=format&' + FL + '=identifier&' + FL + '=language'
        self._url += '&' + FL + '=publisher&' + FL + '=title&' + \
            FL + '=volume'
        self._url += '&' + SORT + '=title&' + SORT + '&' + \
            SORT + '=&rows=500&save=yes&fmt=csv&xmlsearch=Search'
        self.stopthread = threading.Event()

    def run(self):
        logging.error('Searching URL %s', self._url)
        getter = ReadURLDownloader(self._url)
        getter.connect("finished", self.__finished_cb)
        getter.connect("error", self.__error_cb)
        try:
            getter.start(self._path)
        except:
            pass
        self._download_content_type = getter.get_content_type()

    def __error_cb(self, getter, err):
        self._download_content_length = 0
        self._download_content_type = None

    def __finished_cb(self, getter, path, suggested_name):
        if self._download_content_type.startswith('text/html'):
            # got an error page instead
            self._get_csv_error_cb(getter, 'HTTP Error')
            return

        reader = csv.reader(open(path,  'rb'))
        reader.next()  # skip the first header row.
        for row in reader:
            if len(row) < 7:
                return
            entry = {}
            entry['author'] = row[0]
            entry['description'] = row[1]
            entry['format'] = row[2]
            entry['identifier'] = row[3]
            entry['dcterms_language'] = row[4]
            entry['dcterms_publisher'] = row[5]
            entry['title'] = row[6]
            volume = row[7]
            if volume is not None and len(volume) > 0:
                entry['title'] = row[6] + 'Volume ' + volume

            entry['links'] = {}
            url_base = 'http://archive.org/download/' + \
                        row[3] + '/' + row[3]

            formats = entry['format'].split(',')
            if 'DjVu' in formats:
                entry['links']['image/x.djvu'] = 'yes'
            if entry['format'].find('Grayscale LuraTech PDF') > -1:
                # Fake mime type
                entry['links']['application/pdf-bw'] = 'yes'
            if entry['format'].find('PDF') > -1:
                entry['links']['application/pdf'] = 'yes'
            if entry['format'].find('EPUB') > -1:
                entry['links']['application/epub+zip'] = 'yes'
            entry['cover_image'] = 'http://archive.org/download/' + \
                        row[3] + '/page/cover_thumb.jpg'

            self._append_cb(InternetArchiveBook(None, entry, ''))

        os.remove(path)
        self._updated_cb()
        self._ready_cb()

    def stop(self):
        self.stopthread.set()


class InternetArchiveQueryResult(QueryResult):

    # Search in internet archive does not use OPDS
    # because the server implementation is not working very well

    def __init__(self, query, path):
        GObject.GObject.__init__(self)
        self._next_uri = ''
        self._ready = False
        self._booklist = []
        self._cataloglist = []
        self.threads = []

        d_thread = InternetArchiveDownloadThread(query, path,
                                                 self.__updated_cb,
                                                 self.__append_cb,
                                                 self.__ready_cb)
        d_thread.daemon = True
        self.threads.append(d_thread)
        d_thread.start()

    def __updated_cb(self):
        self.emit('updated', False)

    def __append_cb(self, book):
        self._booklist.append(book)

    def __ready_cb(self):
        self._ready = True


class FileDownloaderThread(threading.Thread):

    def __init__(self, url, path, updated_cb, progress_cb):
        threading.Thread.__init__(self)
        self._path = path
        self._updated_cb = updated_cb
        self._progress_cb = progress_cb
        self._getter = ReadURLDownloader(url)
        self._download_content_length = 0
        self._download_content_type = None
        self.stopthread = threading.Event()

    def run(self):
        self._getter.connect("finished", self.__result_cb)
        self._getter.connect("progress", self.__progress_cb)
        self._getter.connect("error", self.__error_cb)
        try:
            self._getter.start(self._path)
        except:
            self._updated_cb(None, None)

        self._download_content_length = \
                self._getter.get_content_length()
        self._download_content_type = self._getter.get_content_type()

    def __result_cb(self, getter, path, suggested_name):
        self._getter = None
        if not self.stopthread.is_set():
            self._updated_cb(path, self._download_content_type)

    def __progress_cb(self, getter, bytes_downloaded):
        if self.stopthread.is_set():
            try:
                getter.cancel()
            except:
                pass
        self._progress_cb(float(bytes_downloaded) / \
                          float(self._download_content_length + 1))

    def __error_cb(self, getter, err):
        self._download_content_length = 0
        self._download_content_type = None
        self._getter = None
        self._updated_cb(None, None)

    def stop(self):
        self.stopthread.set()


class FileDownloader(GObject.GObject):

    __gsignals__ = {
        'updated': (GObject.SignalFlags.RUN_FIRST,
                          None,
                          ([GObject.TYPE_STRING, GObject.TYPE_STRING])),
        'progress': (GObject.SignalFlags.RUN_FIRST,
                          None,
                          ([GObject.TYPE_FLOAT])),
    }

    def __init__(self, url, path):
        GObject.GObject.__init__(self)
        self.threads = []

        d_thread = FileDownloaderThread(url, path, self.__updated_cb,
                                        self.__progress_cb)
        d_thread.daemon = True
        self.threads.append(d_thread)
        d_thread.start()

    def __updated_cb(self, path, content_type):
        self.emit('updated', path, content_type)

    def __progress_cb(self, progress):
        self.emit('progress', progress)

    def stop(self):
        for thread in self.threads:
            thread.stop()
