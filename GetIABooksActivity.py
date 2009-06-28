#! /usr/bin/env python

# Copyright (C) 2009 James D. Simmons
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
import os
import logging
import tempfile
import time
import pygtk
import gtk
import string
import csv
import urllib
from sugar.graphics.toolbutton import ToolButton
from sugar.graphics.menuitem import MenuItem
from sugar.graphics.toolcombobox import ToolComboBox
from sugar.graphics.combobox import ComboBox
from sugar import profile
from sugar.activity import activity
from sugar import network
from sugar.datastore import datastore
from sugar.graphics.alert import NotifyAlert
from gettext import gettext as _
import pango
import dbus
import gobject

_TOOLBAR_BOOKS = 1
COLUMN_CREATOR = 0
COLUMN_DESCRIPTION=1
COLUMN_FORMAT = 2
COLUMN_IDENTIFIER = 3
COLUMN_LANGUAGE = 4
COLUMN_PUBLISHER = 5
COLUMN_SUBJECT = 6
COLUMN_TITLE = 7
COLUMN_VOLUME = 8
COLUMN_TITLE_TRUNC = 9
COLUMN_CREATOR_TRUNC = 10

_logger = logging.getLogger('get-ia-books-activity')

class BooksToolbar(gtk.Toolbar):
    __gtype_name__ = 'BooksToolbar'

    def __init__(self):
        gtk.Toolbar.__init__(self)
        book_search_item = gtk.ToolItem()

        self.search_entry = gtk.Entry()
        self.search_entry.connect('activate', self.search_entry_activate_cb)

        width = int(gtk.gdk.screen_width() / 2)
        self.search_entry.set_size_request(width, -1)

        book_search_item.add(self.search_entry)
        self.search_entry.show()

        self.insert(book_search_item, -1)
        book_search_item.show()

        self._download = ToolButton('go-down')
        self._download.set_tooltip(_('Get Book'))
        self._download.props.sensitive = False
        self._download.connect('clicked', self._get_book_cb)
        self.insert(self._download, -1)
        self._download.show()

        label_attributes = pango.AttrList()
        label_attributes.insert(pango.AttrSize(14000, 0, -1))
        label_attributes.insert(pango.AttrForeground(65535, 65535, 65535, 0, -1))

        downloaded_item = gtk.ToolItem()

        self.downloaded_label = gtk.Label()

        self.downloaded_label.set_attributes(label_attributes)

        self.downloaded_label.set_text('')
        downloaded_item.add(self.downloaded_label)
        self.downloaded_label.show()
        self.search_entry.grab_focus()

        self.insert(downloaded_item, -1)
        downloaded_item.show()

    def set_activity(self, activity):
        self.activity = activity

    def search_entry_activate_cb(self, entry):
        self.activity.find_books(entry.props.text)

    def _get_book_cb(self, button):
        self.activity.get_book()
 
    def _enable_button(self,  state):
        self._download.props.sensitive = state

    def set_downloaded_bytes(self, bytes,  total):
        self.downloaded_label.props.label = '     ' + str(bytes) + ' ' + _('of') +' ' + str(total) + ' ' + _('received')

    def clear_downloaded_bytes(self):
        self.downloaded_label.props.label = ''

class ReadHTTPRequestHandler(network.ChunkedGlibHTTPRequestHandler):
    """HTTP Request Handler for transferring document while collaborating.

    RequestHandler class that integrates with Glib mainloop. It writes
    the specified file to the client in chunks, returning control to the
    mainloop between chunks.

    """
    def translate_path(self, path):
        """Return the filepath to the shared document."""
        return self.server.filepath

class ReadURLDownloader(network.GlibURLDownloader):
    """URLDownloader that provides content-length and content-type."""

    def get_content_length(self):
        """Return the content-length of the download."""
        if self._info is not None:
            return int(self._info.headers.get('Content-Length'))

    def get_content_type(self):
        """Return the content-type of the download."""
        if self._info is not None:
            return self._info.headers.get('Content-type')
        return None

READ_STREAM_SERVICE = 'read-activity-http'

class GetIABooksActivity(activity.Activity):
    def __init__(self, handle):
        "The entry point to the Activity"
        activity.Activity.__init__(self, handle)
 
        toolbox = activity.ActivityToolbox(self)
        activity_toolbar = toolbox.get_activity_toolbar()
        activity_toolbar.remove(activity_toolbar.keep)
        activity_toolbar.keep = None
        self.set_toolbox(toolbox)
        
        self._books_toolbar = BooksToolbar()
        toolbox.add_toolbar(_('Books'), self._books_toolbar)
        self._books_toolbar.set_activity(self)
        self._books_toolbar.show()

        toolbox.show()
        self.scrolled = gtk.ScrolledWindow()
        self.scrolled.set_policy(gtk.POLICY_NEVER, gtk.POLICY_AUTOMATIC)
        self.scrolled.props.shadow_type = gtk.SHADOW_NONE
        self.textview = gtk.TextView()
        self.textview.set_editable(False)
        self.textview.set_cursor_visible(False)
        self.textview.set_wrap_mode(gtk.WRAP_WORD)
        self.textview.set_justification(gtk.JUSTIFY_LEFT)
        self.textview.set_left_margin(50)
        self.textview.set_right_margin(50)
        textbuffer = self.textview.get_buffer()
        textbuffer.set_text(_('Enter words from the Author or Title to begin search') + '.')
        self.scrolled.add(self.textview)
        self.textview.show()
        self.scrolled.show()

        self._download_content_length = 0
        self._download_content_type = None

        self.ls = gtk.ListStore(gobject.TYPE_STRING, gobject.TYPE_STRING, gobject.TYPE_STRING,  gobject.TYPE_STRING,  \
                                gobject.TYPE_STRING,  gobject.TYPE_STRING,  gobject.TYPE_STRING,  gobject.TYPE_STRING,  \
                                gobject.TYPE_STRING,  gobject.TYPE_STRING,  gobject.TYPE_STRING)
        tv = gtk.TreeView(self.ls)
        tv.set_rules_hint(True)
        tv.set_search_column(COLUMN_TITLE)
        selection = tv.get_selection()
        selection.set_mode(gtk.SELECTION_SINGLE)
        selection.connect("changed", self.selection_cb)

        renderer = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_('Title'), renderer, text=COLUMN_TITLE_TRUNC)
        col.set_sort_column_id(COLUMN_TITLE)
        tv.append_column(col)
    
        renderer = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_('Volume'), renderer, text=COLUMN_VOLUME)
        col.set_sort_column_id(COLUMN_VOLUME)
        tv.append_column(col)
    
        renderer = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_('Author'), renderer, text=COLUMN_CREATOR_TRUNC)
        col.set_sort_column_id(COLUMN_CREATOR)
        tv.append_column(col)

        renderer = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_('Language'), renderer, text=COLUMN_LANGUAGE)
        col.set_sort_column_id(COLUMN_LANGUAGE)
        tv.append_column(col)
    
        self.list_scroller = gtk.ScrolledWindow(hadjustment=None, vadjustment=None)
        self.list_scroller.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        self.list_scroller.add(tv)
        
        vbox = gtk.VBox()
        vbox.add(self.scrolled)
        vbox.add(self.list_scroller)
        self.set_canvas(vbox)
        tv.show()
        vbox.show()
        self.list_scroller.show()

        self.toolbox.set_current_toolbar(_TOOLBAR_BOOKS)
        self._books_toolbar.search_entry.grab_focus()

    def selection_cb(self, selection):
        self._books_toolbar.clear_downloaded_bytes()
        tv = selection.get_tree_view()
        model = tv.get_model()
        sel = selection.get_selected()
        if sel:
            model, iter = sel
            label_text = model.get_value(iter,COLUMN_TITLE) + '\n\n'
            self.selected_title = model.get_value(iter,COLUMN_TITLE_TRUNC)
            self.selected_volume = model.get_value(iter,COLUMN_VOLUME) 
            if self.selected_volume != '':
                label_text +=  _('Volume') + ': ' +  self.selected_volume + '\n\n'
            label_text +=  model.get_value(iter,COLUMN_CREATOR) + '\n\n'
            self.selected_author =  model.get_value(iter,COLUMN_CREATOR_TRUNC)
            description = model.get_value(iter,COLUMN_DESCRIPTION)
            if description != '':
                label_text +=  description  + '\n\n'
            subject = model.get_value(iter,COLUMN_SUBJECT) 
            if subject != '':
                label_text +=  _('Subject') + ': ' +  subject + '\n\n'
            label_text +=  _('Publisher') + ': ' + model.get_value(iter,COLUMN_PUBLISHER) + '\n\n'
            label_text +=  _('Language') +': '+ model.get_value(iter,COLUMN_LANGUAGE) + '\n\n'
            self.download_url =   'http://www.archive.org/download/' 
            identifier = model.get_value(iter,COLUMN_IDENTIFIER)
            self.download_url +=  identifier + '/' + identifier + '.djvu'
            label_text +=  _('Download URL') + ': ' + self.download_url
            textbuffer = self.textview.get_buffer()
            textbuffer.set_text(label_text)
            self._books_toolbar._enable_button(True)

    def find_books(self, search_text):
        self._books_toolbar._enable_button(False)
        self._books_toolbar.clear_downloaded_bytes()
        textbuffer = self.textview.get_buffer()
        textbuffer.set_text(_('Performing lookup, please wait') + '...')
        self.book_selected = False
        self.ls.clear()
        search_tuple = search_text.lower().split()
        if len(search_tuple) == 0:
            self._alert(_('Error'), _('You must enter at least one search word.'))
            self._books_toolbar.search_entry.grab_focus()
            return
        FL = urllib.quote('fl[]')
        SORT = urllib.quote('sort[]')
        search_url = 'http://www.archive.org/advancedsearch.php?q=' +  \
            urllib.quote('(title:(' + search_text.lower() + ') OR creator:(' + search_text.lower() +')) AND format:(DJVU)')
        search_url += '&' + FL + '=creator&' + FL + '=description&' + FL + '=format&' + FL + '=identifier&' + FL + '=language'
        search_url += '&' + FL +  '=publisher&' + FL + '=subject&' + FL + '=title&' + FL + '=volume'
        search_url += '&' + SORT + '=title&' + SORT + '&' + SORT + '=&rows=500&save=yes&fmt=csv&xmlsearch=Search'
        gobject.idle_add(self.download_csv,  search_url)
    
    def get_book(self):
        self._books_toolbar._enable_button(False)
        gobject.idle_add(self.download_book,  self.download_url)
        
    def download_csv(self,  url):
        print "get csv from",  url
        path = os.path.join(self.get_activity_root(), 'instance',
                            'tmp%i.csv' % time.time())
        print 'path=', path
        getter = ReadURLDownloader(url)
        getter.connect("finished", self._get_csv_result_cb)
        getter.connect("progress", self._get_csv_progress_cb)
        getter.connect("error", self._get_csv_error_cb)
        _logger.debug("Starting download to %s...", path)
        try:
            getter.start(path)
        except:
            self._alert(_('Error'), _('Connection timed out for CSV: ') + url)
           
        self._download_content_type = getter.get_content_type()

    def _get_csv_progress_cb(self, getter, bytes_downloaded):
        if self._download_content_length > 0:
            _logger.debug("Downloaded %u of %u bytes...",
                          bytes_downloaded, self._download_content_length)
        else:
            _logger.debug("Downloaded %u bytes...",
                          bytes_downloaded)

    def _get_csv_error_cb(self, getter, err):
        _logger.debug("Error getting CSV: %s", err)
        self._alert(_('Error'), _('Error getting CSV') )
        self._download_content_length = 0
        self._download_content_type = None

    def _get_csv_result_cb(self, getter, tempfile, suggested_name):
        print 'Content type:',  self._download_content_type
        if self._download_content_type.startswith('text/html'):
            # got an error page instead
            self._get_csv_error_cb(getter, 'HTTP Error')
            return
        self.process_downloaded_csv(tempfile,  suggested_name)

    def process_downloaded_csv(self,  tempfile,  suggested_name):
        textbuffer = self.textview.get_buffer()
        textbuffer.set_text(_('Finished'))
        reader = csv.reader(open(tempfile,  'rb'))
        reader.next() # skip the first header row.
        for row in reader:
            iter = self.ls.append()
            self.ls.set(iter, 0, row[0],  1,  row[1],  2,  row[2],  3,  row[3],  4,  row[4],  5,  row[5],  \
                        6,  row[6],  7,  row[7],  8,  row[8],   \
                        COLUMN_TITLE_TRUNC,  self.truncate(row[COLUMN_TITLE],  75),  \
                        COLUMN_CREATOR_TRUNC,  self.truncate(row[COLUMN_CREATOR],  40))
        os.remove(tempfile)

    def download_book(self,  url):
        print "get book from",  url
        path = os.path.join(self.get_activity_root(), 'instance',
                            'tmp%i' % time.time())
        getter = ReadURLDownloader(url)
        getter.connect("finished", self._get_book_result_cb)
        getter.connect("progress", self._get_book_progress_cb)
        getter.connect("error", self._get_book_error_cb)
        _logger.debug("Starting download to %s...", path)
        try:
            getter.start(path)
        except:
            self._alert(_('Error'), _('Connection timed out for ') + self.selected_title)
           
        self._download_content_length = getter.get_content_length()
        self._download_content_type = getter.get_content_type()

    def _get_book_result_cb(self, getter, tempfile, suggested_name):
        print 'Content type:',  self._download_content_type
        if self._download_content_type.startswith('text/html'):
            # got an error page instead
            self._get_book_error_cb(getter, 'HTTP Error')
            return
        self.process_downloaded_book(tempfile,  suggested_name)

    def _get_book_progress_cb(self, getter, bytes_downloaded):
        if self._download_content_length > 0:
            _logger.debug("Downloaded %u of %u bytes...",
                          bytes_downloaded, self._download_content_length)
        else:
            _logger.debug("Downloaded %u bytes...",
                          bytes_downloaded)
        total = self._download_content_length
        self._books_toolbar.set_downloaded_bytes(bytes_downloaded,  total)
        while gtk.events_pending():
            gtk.main_iteration()

    def _get_book_error_cb(self, getter, err):
        _logger.debug("Error getting document: %s", err)
        self._alert(_('Error'), _('Could not download ') + self.selected_title + _(' path in catalog may be incorrect.'))
        self._download_content_length = 0
        self._download_content_type = None

    def process_downloaded_book(self,  tempfile,  suggested_name):
        _logger.debug("Got document %s (%s)", tempfile, suggested_name)
        self.create_journal_entry(tempfile)

    def create_journal_entry(self,  tempfile):
        journal_entry = datastore.create()
        journal_title = self.selected_title
        if self.selected_volume != '':
            journal_title +=  ' ' + _('Volume') + ' ' +  self.selected_volume
        if self.selected_author != '':
            journal_title = journal_title  + ', by ' + self.selected_author
        journal_entry.metadata['title'] = journal_title
        journal_entry.metadata['title_set_by_user'] = '1'
        journal_entry.metadata['keep'] = '0'
        journal_entry.metadata['mime_type'] = 'image/vnd.djvu'
        journal_entry.metadata['buddies'] = ''
        journal_entry.metadata['preview'] = ''
        journal_entry.metadata['icon-color'] = profile.get_color().to_string()
        textbuffer = self.textview.get_buffer()
        journal_entry.metadata['description'] = textbuffer.get_text(textbuffer.get_start_iter(),  textbuffer.get_end_iter())
        journal_entry.file_path = tempfile
        datastore.write(journal_entry)
        os.remove(tempfile)
        self._alert(_('Success'), self.selected_title + _(' added to Journal.'))

    def truncate(self,  str,  length):
        if len(str) > length:
            return str[0:length-1] + '...'
        else:
            return str
    
    def _alert(self, title, text=None):
        alert = NotifyAlert(timeout=20)
        alert.props.title = title
        alert.props.msg = text
        self.add_alert(alert)
        alert.connect('response', self._alert_cancel_cb)
        alert.show()

    def _alert_cancel_cb(self, alert, response_id):
        self.remove_alert(alert)
        self.textview.grab_focus()
