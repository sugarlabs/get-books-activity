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

OLD_TOOLBAR = False
try:
    from sugar.graphics.toolbarbox import ToolbarBox, ToolbarButton
    from sugar.activity.widgets import StopButton
    from sugar.activity.widgets import ActivityToolbarButton
except ImportError:
    OLD_TOOLBAR = True

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

_MIMETYPES = { 'PDF' : u'application/pdf', 'EPUB' : u'application/epub+zip' }
_SOURCES = {'Internet Archive' : 'internet-archive', 'Feedbooks' : 'feedbooks'}

_logger = logging.getLogger('get-ia-books-activity')


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
        self.max_participants = 1

        self.selected_book = None
        self.queryresults = None
        self._getter = None

        if OLD_TOOLBAR:

            toolbox = activity.ActivityToolbox(self)
            activity_toolbar = toolbox.get_activity_toolbar()

            self.set_toolbox(toolbox)
            self._books_toolbar = gtk.Toolbar()
            self._add_search_controls(self._books_toolbar)
            self.toolbox.add_toolbar(_('Books'), self._books_toolbar)
            self._books_toolbar.show()
            toolbox.show()
            toolbox.set_current_toolbar(1)

        else:
            toolbar_box = ToolbarBox()
            activity_button = ActivityToolbarButton(self)
            activity_toolbar = activity_button.page
            toolbar_box.toolbar.insert(activity_button, 0)
            self._add_search_controls(toolbar_box.toolbar)

            separator = gtk.SeparatorToolItem()
            separator.props.draw = False
            separator.set_expand(True)
            toolbar_box.toolbar.insert(separator, -1)

            toolbar_box.toolbar.insert(StopButton(self), -1)

            self.set_toolbar_box(toolbar_box)
            toolbar_box.show_all()
            self._books_toolbar = toolbar_box.toolbar

        activity_toolbar.keep.props.visible = False
        self._create_controls()


    def _add_search_controls(self, toolbar):
        book_search_item = gtk.ToolItem()

        toolbar.source_combo = ComboBox()
        toolbar.source_combo.props.sensitive = True
        toolbar.__source_changed_cb_id = \
            toolbar.source_combo.connect('changed', self.__source_changed_cb)
        combotool = ToolComboBox(toolbar.source_combo)
        toolbar.insert(combotool, -1)
        combotool.show()

        toolbar.search_entry = iconentry.IconEntry()
        toolbar.search_entry.set_icon_from_name(iconentry.ICON_ENTRY_PRIMARY,
                                                'system-search')
        toolbar.search_entry.add_clear_button()
        toolbar.search_entry.connect('activate', self.__search_entry_activate_cb)
        width = int(gtk.gdk.screen_width() / 3)
        toolbar.search_entry.set_size_request(width, -1)
        book_search_item.add(toolbar.search_entry)
        toolbar.search_entry.show()
        toolbar.insert(book_search_item, -1)
        book_search_item.show()

        spacer = gtk.SeparatorToolItem()
        spacer.props.draw = False
        spacer.set_expand(True)
        toolbar.insert(spacer, -1)
        spacer.show()

        toolbar._download = ToolButton('go-down')
        toolbar._download.set_tooltip(_('Get Book'))
        toolbar._download.props.sensitive = False
        toolbar._download.connect('clicked', self.__get_book_cb)
        toolbar.insert(toolbar._download, -1)
        toolbar._download.show()

        toolbar.format_combo = ComboBox()
        for key in _MIMETYPES.keys():
            toolbar.format_combo.append_item(_MIMETYPES[key], key)
        toolbar.format_combo.set_active(0)
        toolbar.format_combo.props.sensitive = False
        toolbar.__format_changed_cb_id = \
                toolbar.format_combo.connect('changed',
                self.__format_changed_cb)
        combotool = ToolComboBox(toolbar.format_combo)
        toolbar.insert(combotool, -1)
        combotool.show()

        self._device_manager = devicemanager.DeviceManager()
        self._refresh_sources(toolbar)
        self._device_manager.connect('device-added', self.__device_added_cb)
        self._device_manager.connect('device-removed', self.__device_removed_cb)

        toolbar.search_entry.grab_focus()
        return toolbar

    def update_format_combo(self, links):
        self._books_toolbar.format_combo.handler_block(self._books_toolbar.__format_changed_cb_id)
        self._books_toolbar.format_combo.remove_all()
        for key in _MIMETYPES.keys():
            if _MIMETYPES[key] in links.keys():
                self._books_toolbar.format_combo.append_item(_MIMETYPES[key], key)
        self._books_toolbar.format_combo.set_active(0)
        self._books_toolbar.format_combo.handler_unblock(self._books_toolbar.__format_changed_cb_id)

    def get_search_terms(self):
        return self._books_toolbar.search_entry.props.text

    def __source_changed_cb(self, widget):
        self.emit('source-changed')

    def __device_added_cb(self, mgr):
        _logger.debug('Device was added')
        self._refresh_sources(self._books_toolbar)

    def __device_removed_cb(self, mgr):
        _logger.debug('Device was removed')
        self._refresh_sources(self._books_toolbar)

    def _refresh_sources(self, books_toolbar):
        books_toolbar.source_combo.handler_block(books_toolbar.__source_changed_cb_id)

        books_toolbar.source_combo.remove_all() #TODO: Do not blindly clear this
        for key in _SOURCES.keys():
            books_toolbar.source_combo.append_item(_SOURCES[key], key)

        devices = self._device_manager.get_devices()

        if len(devices):
            books_toolbar.source_combo.append_separator()

        for device in devices:
            mount_point = device[1].GetProperty('volume.mount_point')
            label = device[1].GetProperty('volume.label')
            if label == '' or label is None:
                capacity = int(device[1].GetProperty('volume.partition.media_size'))
                label =  (_('%.2f GB Volume') % (capacity/(1024.0**3)))
            _logger.debug('Adding device %s' % (label))
            books_toolbar.source_combo.append_item(mount_point, label)

        books_toolbar.source_combo.set_active(0)

        books_toolbar.source_combo.handler_unblock(books_toolbar.__source_changed_cb_id)

    def __format_changed_cb(self, combo):
        self.show_book_data()

    def __search_entry_activate_cb(self, entry):
        self.find_books(entry.props.text)

    def __get_book_cb(self, button):
        self.get_book()
 
    def enable_button(self,  state):
        self._books_toolbar._download.props.sensitive = state
        self._books_toolbar.format_combo.props.sensitive = state

    def _create_controls(self):
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
        vadjustment = self.list_scroller.get_vadjustment()
        vadjustment.connect('value-changed', self.__vadjustment_value_changed_cb)
        self.list_scroller.add(self.listview)
        
        self.progressbox = gtk.HBox(spacing = 20)
        
        self.progressbar = gtk.ProgressBar() #TODO: Add a way to cancel download
        self.progressbar.set_orientation(gtk.PROGRESS_LEFT_TO_RIGHT)
        self.progressbar.set_fraction(0.0)

        self.progressbox.pack_start(self.progressbar, expand = True, fill = True)
        self.cancel_btn = gtk.Button(stock = gtk.STOCK_CANCEL)
        self.cancel_btn.connect('clicked', self.__cancel_btn_clicked_cb)
        self.progressbox.pack_start(self.cancel_btn, expand = False, fill = False)

        vbox = gtk.VBox()
        vbox.pack_start(self.progressbox,  False,  False,  10)
        vbox.pack_start(self.scrolled)
        vbox.pack_end(self.list_scroller)
        self.set_canvas(vbox)
        self.listview.show()
        vbox.show()
        self.list_scroller.show()
        self.progressbox.hide()

        self._books_toolbar.search_entry.grab_focus()

    def can_close(self):
        self._lang_code_handler.close()
        if self.queryresults is not None:
            self.queryresults.cancel()
            self.queryresults = None
        return True

    def selection_cb(self, widget):
        self.clear_downloaded_bytes()
        selected_book = self.listview.get_selected_book()
        if selected_book:
            self.update_format_combo(selected_book.get_download_links())
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

        self.enable_button(True)

    def find_books(self, search_text = ''):
        source = self._books_toolbar.source_combo.props.value

        self.enable_button(False)
        self.clear_downloaded_bytes()
        self.book_selected = False
        self.listview.clear()

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
            self.queryresults = opds.FeedBooksQueryResult(search_text, self.window)    
        elif source == 'internet-archive':
            if search_text is None:
                return
            elif len(search_text) == 0:
                self._alert(_('Error'), _('You must enter at least one search word.'))
                self._books_toolbar.search_entry.grab_focus()
                return
            self.queryresults = opds.InternetArchiveQueryResult(search_text, self.window)
        else:
            self.queryresults = opds.LocalVolumeQueryResult( \
                        source, search_text, self.window)

        textbuffer = self.textview.get_buffer()
        textbuffer.set_text(_('Performing lookup, please wait...'))

        self.queryresults.connect('updated', self.__query_updated_cb)

    def __query_updated_cb(self, query, midway):
        self.listview.populate(self.queryresults)

        textbuffer = self.textview.get_buffer()
        if len(self.queryresults) == 0:
            textbuffer.set_text(_('Sorry, no books could be found.'))
        elif not midway:
            textbuffer.set_text('')

    def __source_changed_cb(self, widget):
        search_terms = self.get_search_terms()
        if search_terms == '':
            self.find_books(None)
        else:
            self.find_books(search_terms)

    def __vadjustment_value_changed_cb(self, vadjustment):

        if not self.queryresults.is_ready():
            return
        try:
            # Use various tricks to update resultset as user scrolls down
            if ((vadjustment.props.upper - vadjustment.props.lower) > 1000 \
                and (vadjustment.props.upper - vadjustment.props.value - \
                vadjustment.props.page_size)/(vadjustment.props.upper - \
                vadjustment.props.lower) < 0.3) or ((vadjustment.props.upper \
                - vadjustment.props.value - vadjustment.props.page_size) < 200):
                if self.queryresults.has_next():
                    self.queryresults.update_with_next()
        finally:
            return

    def __cancel_btn_clicked_cb(self, btn):
        if self._getter is not None:
            try:
                self._getter.cancel()
            except:
                _logger.debug('Got an exception while trying to cancel download')
            self.progressbox.hide()
            self.listview.props.sensitive = True
            _logger.debug('Download was canceled by the user.')

    def get_book(self):
        self.enable_button(False)
        self.progressbox.show_all()
        gobject.idle_add(self.download_book,  self.download_url)
        
    def download_book(self,  url):
        self.listview.props.sensitive = False
        path = os.path.join(self.get_activity_root(), 'instance',
                            'tmp%i' % time.time())
        self._getter = ReadURLDownloader(url)
        self._getter.connect("finished", self._get_book_result_cb)
        self._getter.connect("progress", self._get_book_progress_cb)
        self._getter.connect("error", self._get_book_error_cb)
        _logger.debug("Starting download to %s...", path)
        try:
            self._getter.start(path)
        except:
            self._alert(_('Error'), _('Connection timed out for ') + self.selected_title)
           
        self._download_content_length = self._getter.get_content_length()
        self._download_content_type = self._getter.get_content_type()

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
        self.enable_button(True)
        self.progressbox.hide()
        _logger.debug("Error getting document: %s", err)
        self._alert(_('Error: Could not download %s . The path in the catalog seems to be incorrect') % self.selected_title)
        #self._alert(_('Error'), _('Could not download ') + self.selected_title + _(' path in catalog is incorrect.  ' \
        #                                                                           + '  If you tried to download B/W PDF try another format.'))
        self._download_content_length = 0
        self._download_content_type = None
        self._getter = None

    def process_downloaded_book(self,  tempfile,  suggested_name):
        _logger.debug("Got document %s (%s)", tempfile, suggested_name)
        self.create_journal_entry(tempfile)
        self._getter = None

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
        self.progressbox.hide()
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
