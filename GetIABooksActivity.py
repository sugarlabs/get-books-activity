#! /usr/bin/env python

# Copyright (C) 2009 James D. Simmons
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
from sugar.graphics import iconentry
from sugar import profile
from sugar.activity import activity
from sugar import network
from sugar.datastore import datastore
from sugar.graphics.alert import NotifyAlert
from gettext import gettext as _
import pango
import dbus
import gobject

from listview import ListView
import opds
import languagenames
import devicemanager

_TOOLBAR_BOOKS = 1
_MIMETYPES = { 'PDF' : u'application/pdf', 'EPUB' : u'application/epub+zip' }
_SOURCES = {'Internet Archive' : 'internet-archive', 'Feedbooks' : 'feedbooks'}

_logger = logging.getLogger('get-ia-books-activity')

class BooksToolbar(gtk.Toolbar):
    __gtype_name__ = 'BooksToolbar'
    __gsignals__ = {
        'source-changed': (gobject.SIGNAL_RUN_FIRST,
                          gobject.TYPE_NONE,
                          ([])),
    }
    def __init__(self):
        gtk.Toolbar.__init__(self)
        book_search_item = gtk.ToolItem()

        self.source_combo = ComboBox()
        self.source_combo.props.sensitive = True
        self.source_combo.connect('changed', self.__source_changed_cb)
        combotool = ToolComboBox(self.source_combo)
        self.insert(combotool, -1)
        combotool.show()

        self.search_entry = iconentry.IconEntry()
        self.search_entry.set_icon_from_name(iconentry.ICON_ENTRY_PRIMARY,
                                                'system-search')
        self.search_entry.add_clear_button()
        self.search_entry.connect('activate', self.search_entry_activate_cb)

        width = int(gtk.gdk.screen_width() / 3)
        self.search_entry.set_size_request(width, -1)

        book_search_item.add(self.search_entry)
        self.search_entry.show()

        self.insert(book_search_item, -1)
        book_search_item.show()

        spacer = gtk.SeparatorToolItem()
        spacer.props.draw = False
        spacer.set_expand(True)
        self.insert(spacer, -1)
        spacer.show()

        self._download = ToolButton('go-down')
        self._download.set_tooltip(_('Get Book'))
        self._download.props.sensitive = False
        self._download.connect('clicked', self._get_book_cb)
        self.insert(self._download, -1)
        self._download.show()

        self.format_combo = ComboBox()
        for key in _MIMETYPES.keys():
            self.format_combo.append_item(_MIMETYPES[key], key)
        self.format_combo.set_active(0)
        self.format_combo.props.sensitive = False
        self.format_combo.connect('changed', self.format_changed_cb)
        combotool = ToolComboBox(self.format_combo)
        self.insert(combotool, -1)
        combotool.show()

        self._device_manager = devicemanager.DeviceManager()

        self._refresh_sources()

        self._device_manager.connect('device-added', self.__device_added_cb)
        self._device_manager.connect('device-removed', self.__device_removed_cb)

        self.search_entry.grab_focus()

    def get_search_terms(self):
        return self.search_entry.props.text

    def __source_changed_cb(self, widget):
        self.emit('source-changed')

    def __device_added_cb(self):
        self._refresh_sources()

    def __device_removed_cb(self):
        self._refresh_sources()

    def _refresh_sources(self):
        self.source_combo.remove_all() #TODO: Do not blindly clear this

        for key in _SOURCES.keys():
            self.source_combo.append_item(_SOURCES[key], key)

        self.source_combo.append_separator()

        devices = self._device_manager.get_devices()
        for device in devices:
            mount_point = device[1].GetProperty('volume.mount_point')
            label = device[1].GetProperty('volume.label')
            if label == '' or label is None:
                capacity = int(device[1].GetProperty('volume.partition.media_size'))
                label =  (_('%.2f GB Volume') % (capacity/(1024.0**3)))
            self.source_combo.append_item(mount_point, label)

        self.source_combo.set_active(0) 

    def set_activity(self, activity):
        self.activity = activity

    def format_changed_cb(self, combo):
        if self.activity != None:
            self.activity.show_book_data()

    def search_entry_activate_cb(self, entry):
        self.activity.find_books(entry.props.text)

    def _get_book_cb(self, button):
        self.activity.get_book()
 
    def enable_button(self,  state):
        self._download.props.sensitive = state
        self.format_combo.props.sensitive = state

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

        self.selected_book = None
        self.queryresults = None
 
        toolbox = activity.ActivityToolbox(self)
        activity_toolbar = toolbox.get_activity_toolbar()
        activity_toolbar.keep.props.visible = False
        activity_toolbar.share.props.visible = False
        self.set_toolbox(toolbox)
        
        self._books_toolbar = BooksToolbar()
        self._books_toolbar.connect('source-changed', self.__source_changed_cb)
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
        textbuffer.set_text(_('Enter words from the Author or Title to begin search.'))
        self.scrolled.add(self.textview)
        self.textview.show()
        self.scrolled.show()

        self._download_content_length = 0
        self._download_content_type = None

        self._lang_code_handler = languagenames.LanguageNames()

        self.listview = ListView(self._lang_code_handler)
        self.listview.connect('selection-changed', self.selection_cb)
            
        self.list_scroller = gtk.ScrolledWindow(hadjustment=None, vadjustment=None)
        self.list_scroller.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        self.list_scroller.add(self.listview)
        
        self.progressbar = gtk.ProgressBar() #TODO: Add a way to cancel download
        self.progressbar.set_orientation(gtk.PROGRESS_LEFT_TO_RIGHT)
        self.progressbar.set_fraction(0.0)
        
        vbox = gtk.VBox()
        vbox.pack_start(self.progressbar,  False,  False,  10)
        vbox.pack_start(self.scrolled)
        vbox.pack_end(self.list_scroller)
        self.set_canvas(vbox)
        self.listview.show()
        vbox.show()
        self.list_scroller.show()
        self.progressbar.hide()

        self.toolbox.set_current_toolbar(_TOOLBAR_BOOKS)
        self._books_toolbar.search_entry.grab_focus()

    def can_close(self):
        self._lang_code_handler.close()
        return True

    def selection_cb(self, widget):
        self.clear_downloaded_bytes()
        selected_book = self.listview.get_selected_book()
        if selected_book:
            self.selected_book = selected_book
            self.show_book_data()

    def show_book_data(self):
        self.book_data = _('Title:\t\t') + self.selected_book.get_title() + '\n\n'
        self.selected_title = self.selected_book.get_title()
        self.book_data +=  _('Author:\t\t') + self.selected_book.get_author() + '\n\n'
        self.selected_author =  self.selected_book.get_author()
        self.book_data +=  _('Publisher:\t') +  self.selected_book.get_publisher() + '\n\n'
        self.book_data +=  _('Language:\t') + \
            self._lang_code_handler.get_full_language_name(self.selected_book.get_language()) + '\n\n'
        self.download_url =  self.selected_book.get_download_links()[self._books_toolbar.format_combo.props.value]

        textbuffer = self.textview.get_buffer()
        textbuffer.set_text(self.book_data + _('Link:\t\t') + self.download_url)

        self._books_toolbar.enable_button(True)

    def find_books(self, search_text = ''):
        source = self._books_toolbar.source_combo.props.value

        if self.queryresults is not None:
            self.queryresults.cancel()
            self.queryresults = None

        # This must be kept in sync with the sources list
        if source == 'feedbooks':
            if search_text is None:
                return
            elif len(search_text) == 0:
                self._alert(_('Error'), _('You must enter at least one search word.'))
                self._books_toolbar.search_entry.grab_focus()
                return
            self.queryresults = opds.FeedBooksQueryResult(search_text)    
        elif source == 'internet-archive':
            if search_text is None:
                return
            elif len(search_text) == 0:
                self._alert(_('Error'), _('You must enter at least one search word.'))
                self._books_toolbar.search_entry.grab_focus()
                return
            self.queryresults = opds.InternetArchiveQueryResult(search_text)
        else:
            self.queryresults = opds.LocalVolumeQueryResult( \
                        self._books_toolbar.source_combo.props.value, search_text)

        self._books_toolbar.enable_button(False)
        self.clear_downloaded_bytes()
        textbuffer = self.textview.get_buffer()
        textbuffer.set_text(_('Performing lookup, please wait...'))
        self.book_selected = False
        self.listview.clear()

        self.queryresults.connect('completed', self.__query_completed_cb)

    def __query_completed_cb(self, query):
        self.listview.populate(self.queryresults)

        textbuffer = self.textview.get_buffer()
        if len(self.queryresults) > 0:
            textbuffer.set_text('')
        else:
            textbuffer.set_text(_('Sorry, no books could be found.'))

    def __source_changed_cb(self, widget):
        search_terms = self._books_toolbar.get_search_terms()
        if search_terms == '':
            self.find_books(None)
        else:
            self.find_books(search_terms)

    def get_book(self):
        self._books_toolbar.enable_button(False)
        self.progressbar.show()
        gobject.idle_add(self.download_book,  self.download_url)
        
    def download_book(self,  url):
        self.listview.props.sensitive = False
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
        self.listview.props.sensitive = True
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
        self.set_downloaded_bytes(bytes_downloaded,  total)
        while gtk.events_pending():
            gtk.main_iteration()

    def set_downloaded_bytes(self, bytes,  total):
        fraction = float(bytes) / float(total)
        self.progressbar.set_fraction(fraction)
        
    def clear_downloaded_bytes(self):
        self.progressbar.set_fraction(0.0)

    def _get_book_error_cb(self, getter, err):
        self.listview.props.sensitive = True
        self._books_toolbar.enable_button(True)
        self.progressbar.hide()
        _logger.debug("Error getting document: %s", err)
        self._alert(_('Error: Could not download %s . The path in the catalog seems to be incorrect') % self.selected_title)
        #self._alert(_('Error'), _('Could not download ') + self.selected_title + _(' path in catalog is incorrect.  ' \
        #                                                                           + '  If you tried to download B/W PDF try another format.'))
        self._download_content_length = 0
        self._download_content_type = None

    def process_downloaded_book(self,  tempfile,  suggested_name):
        _logger.debug("Got document %s (%s)", tempfile, suggested_name)
        self.create_journal_entry(tempfile)

    def create_journal_entry(self,  tempfile):
        journal_entry = datastore.create()
        journal_title = self.selected_title
        if self.selected_author != '':
            journal_title = journal_title  + ', by ' + self.selected_author
        journal_entry.metadata['title'] = journal_title
        journal_entry.metadata['title_set_by_user'] = '1'
        journal_entry.metadata['keep'] = '0'
        journal_entry.metadata['mime_type'] = self._books_toolbar.format_combo.props.value
        journal_entry.metadata['buddies'] = ''
        journal_entry.metadata['preview'] = ''
        journal_entry.metadata['icon-color'] = profile.get_color().to_string()
        textbuffer = self.textview.get_buffer()
        journal_entry.metadata['description'] = textbuffer.get_text(textbuffer.get_start_iter(),  textbuffer.get_end_iter())
        journal_entry.file_path = tempfile
        datastore.write(journal_entry)
        os.remove(tempfile)
        self.progressbar.hide()
        self._alert(_('Success: %s was added to Journal.') % self.selected_title)
        #self._alert(_('Success'), self.selected_title + _(' added to Journal.'))

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
