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

from pprint import pformat

import gi
gi.require_version('Gtk', '3.0')

from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import GdkPixbuf
from gi.repository import GObject
from gi.repository import Pango

from sugar3.graphics.toolbarbox import ToolbarBox
from sugar3.activity.widgets import StopButton
from sugar3.graphics import style
from sugar3.graphics.toolbutton import ToolButton
from sugar3.graphics.toggletoolbutton import ToggleToolButton
from sugar3.graphics.toolcombobox import ToolComboBox
from sugar3.graphics.combobox import ComboBox
from sugar3.graphics import iconentry
from sugar3 import profile
from sugar3.activity import activity
from sugar3.activity.widgets import ToolbarButton
from sugar3.bundle.activitybundle import ActivityBundle
from sugar3.datastore import datastore
from sugar3.graphics.alert import NotifyAlert
from sugar3.graphics.alert import Alert
from sugar3.graphics.icon import Icon
from gettext import gettext as _

try:
    from sugar3.activity.activity import get_bundle, launch_bundle
    _HAS_BUNDLE_LAUNCHER = True
except ImportError:
    _HAS_BUNDLE_LAUNCHER = False

import dbus
import ConfigParser
import base64
import urllib2
import socket


from listview import ListView
import opds
import languagenames
import devicemanager

_MIMETYPES = {'PDF': u'application/pdf', 'PDF BW': u'application/pdf-bw',
                'EPUB': u'application/epub+zip', 'DJVU': u'image/x.djvu'}
_SOURCES = {}
_SOURCES_CONFIG = {}

READ_STREAM_SERVICE = 'read-activity-http'

# directory exists if powerd is running.  create a file here,
# named after our pid, to inhibit suspend.
POWERD_INHIBIT_DIR = '/var/run/powerd-inhibit-suspend'


class GetIABooksActivity(activity.Activity):

    def __init__(self, handle):
        "The entry point to the Activity"
        activity.Activity.__init__(self, handle, False)
        self.max_participants = 1

        self._sequence = 0
        self.selected_book = None
        self.queryresults = None
        self._getter = None
        self.show_images = True
        self.languages = {}
        self._lang_code_handler = languagenames.LanguageNames()
        self.catalogs_configuration = {}
        self.catalog_history = []

        if os.path.exists('/etc/get-books.cfg'):
            self._read_configuration('/etc/get-books.cfg')
        else:
            self._read_configuration()

        toolbar_box = ToolbarBox()
        activity_button = ToolButton()
        color = profile.get_color()
        bundle = ActivityBundle(activity.get_bundle_path())
        icon = Icon(file=bundle.get_icon(), xo_color=color)
        activity_button.set_icon_widget(icon)
        activity_button.show()

        toolbar_box.toolbar.insert(activity_button, 0)
        self._add_search_controls(toolbar_box.toolbar)

        separator = Gtk.SeparatorToolItem()
        separator.props.draw = False
        separator.set_expand(True)
        toolbar_box.toolbar.insert(separator, -1)

        toolbar_box.toolbar.insert(StopButton(self), -1)

        self.set_toolbar_box(toolbar_box)
        toolbar_box.show_all()
        self._books_toolbar = toolbar_box.toolbar

        self._create_controls()

        self.using_powerd = os.access(POWERD_INHIBIT_DIR, os.W_OK)

        self.__book_downloader = self.__image_downloader = None

    def get_path(self):
        self._sequence += 1
        return os.path.join(self.get_activity_root(),
                            'instance', '%03d.tmp' % self._sequence)

    def _inhibit_suspend(self):
        if self.using_powerd:
            fd = open(POWERD_INHIBIT_DIR + "/%u" % os.getpid(), 'w')
            logging.error("inhibit_suspend file is %s", (POWERD_INHIBIT_DIR \
                    + "/%u" % os.getpid()))
            fd.close()
            return True

        return False

    def _allow_suspend(self):
        if self.using_powerd:
            if os.path.exists(POWERD_INHIBIT_DIR + "/%u" % os.getpid()):
                os.unlink(POWERD_INHIBIT_DIR + "/%u" % os.getpid())
            logging.error("allow_suspend unlinking %s", (POWERD_INHIBIT_DIR \
                    + "/%u" % os.getpid()))
            return True

        return False

    def _read_configuration(self, file_name='get-books.cfg'):
        logging.error('Reading configuration from file %s', file_name)
        config = ConfigParser.ConfigParser()
        config.readfp(open(file_name))
        if config.has_option('GetBooks', 'show_images'):
            self.show_images = config.getboolean('GetBooks', 'show_images')
        self.languages = {}
        if config.has_option('GetBooks', 'languages'):
            languages_param = config.get('GetBooks', 'languages')
            for language in languages_param.split(','):
                lang_code = language.strip()
                if len(lang_code) > 0:
                    self.languages[lang_code] = \
                    self._lang_code_handler.get_full_language_name(lang_code)

        for section in config.sections():
            if section != 'GetBooks' and not section.startswith('Catalogs'):
                name = config.get(section, 'name')
                _SOURCES[section] = name
                repo_config = {}
                repo_config['query_uri'] = config.get(section, 'query_uri')
                repo_config['opds_cover'] = config.get(section, 'opds_cover')
                if config.has_option(section, 'summary_field'):
                    repo_config['summary_field'] = \
                        config.get(section, 'summary_field')
                else:
                    repo_config['summary_field'] = None
                if config.has_option(section, 'blacklist'):
                    blacklist = config.get(section, 'blacklist')
                    repo_config['blacklist'] = blacklist.split(',')
                    # TODO strip?
                else:
                    repo_config['blacklist'] = []

                _SOURCES_CONFIG[section] = repo_config

        logging.error('_SOURCES %s', pformat(_SOURCES))
        logging.error('_SOURCES_CONFIG %s', pformat(_SOURCES_CONFIG))

        for section in config.sections():
            if section.startswith('Catalogs'):
                catalog_source = section.split('_')[1]
                if not catalog_source in _SOURCES_CONFIG:
                    logging.error('There are not a source for the catalog ' +
                            'section  %s', section)
                    break
                source_config = _SOURCES_CONFIG[catalog_source]
                opds_cover = source_config['opds_cover']
                for catalog in config.options(section):
                    catalog_config = {}
                    catalog_config['query_uri'] = config.get(section, catalog)
                    catalog_config['opds_cover'] = opds_cover
                    catalog_config['source'] = catalog_source
                    catalog_config['name'] = catalog
                    catalog_config['summary_field'] = \
                        source_config['summary_field']
                    self.catalogs_configuration[catalog] = catalog_config

        self.source = _SOURCES_CONFIG.keys()[0]

        self.filter_catalogs_by_source()

        logging.error('languages %s', pformat(self.languages))
        logging.error('catalogs %s', pformat(self.catalogs))

    def _add_search_controls(self, toolbar):
        book_search_item = Gtk.ToolItem()
        toolbar.search_entry = iconentry.IconEntry()
        toolbar.search_entry.set_icon_from_name(iconentry.ICON_ENTRY_PRIMARY,
                'system-search')
        toolbar.search_entry.add_clear_button()
        toolbar.search_entry.connect('activate',
                self.__search_entry_activate_cb)
        width = int(Gdk.Screen.width() / 4)
        toolbar.search_entry.set_size_request(width, -1)
        book_search_item.add(toolbar.search_entry)
        toolbar.search_entry.show()
        toolbar.insert(book_search_item, -1)
        book_search_item.show()

        toolbar.source_combo = ComboBox()
        toolbar.source_combo.props.sensitive = True
        toolbar.source_changed_cb_id = \
            toolbar.source_combo.connect('changed', self.__source_changed_cb)
        combotool = ToolComboBox(toolbar.source_combo)
        toolbar.insert(combotool, -1)	
        combotool.show()

        self.bt_catalogs = ToggleToolButton('books')
        self.bt_catalogs.set_tooltip(_('Catalogs'))
        toolbar.insert(self.bt_catalogs, -1)
        self.bt_catalogs.connect('toggled', self.__toggle_cats_cb)
        if len(self.catalogs) > 0:
            self.bt_catalogs.show()

        if len(self.languages) > 0:
            toolbar.config_toolbarbutton = ToolbarButton()
            toolbar.config_toolbarbutton.props.icon_name = 'preferences-system'
            toolbar.config_toolbarbox = Gtk.Toolbar()
            toolbar.config_toolbarbutton.props.page = toolbar.config_toolbarbox
            toolbar.language_combo = ComboBox()
            toolbar.language_combo.props.sensitive = True
            combotool = ToolComboBox(toolbar.language_combo)
            toolbar.language_combo.append_item('all', _('Any language'))
            for key in self.languages.keys():
                toolbar.language_combo.append_item(key, self.languages[key])
            toolbar.language_combo.set_active(0)
            toolbar.config_toolbarbutton.props.page.insert(combotool, -1)
            toolbar.insert(toolbar.config_toolbarbutton, -1)
            toolbar.config_toolbarbutton.show()
            combotool.show()
            toolbar.language_changed_cb_id = \
                toolbar.language_combo.connect('changed',
                self.__language_changed_cb)

        self._device_manager = devicemanager.DeviceManager()
        self._refresh_sources(toolbar)
        self._device_manager.connect('device-changed',
                self.__device_changed_cb)

        toolbar.search_entry.grab_focus()
        return toolbar

    def __bt_catalogs_clicked_cb(self, button):
        palette = button.get_palette()
        palette.popup(immediate=True, state=palette.SECONDARY)

    def __switch_catalog_cb(self, catalog_name):
        catalog_config = self.catalogs[catalog_name.decode('utf-8')]
        self.__activate_catalog_cb(None, catalog_config)

    def __activate_catalog_cb(self, menu, catalog_config):
        query_language = self.get_query_language()

        self.enable_button(False)
        self.clear_downloaded_bytes()
        self.book_selected = False
        self.listview.handler_block(self.selection_cb_id)
        self.listview.clear()
        self.listview.handler_unblock(self.selection_cb_id)
        logging.error('SOURCE %s', catalog_config['source'])
        self._books_toolbar.search_entry.props.text = ''
        self.source = catalog_config['source']
        position = _SOURCES_CONFIG[self.source]['position']
        self._books_toolbar.source_combo.set_active(position)

        if self.queryresults is not None:
            self.queryresults.cancel()
            self.queryresults = None

        self.queryresults = opds.RemoteQueryResult(catalog_config,
                '', query_language)
        self.show_message(_('Performing lookup, please wait...'))
        # README: I think we should create some global variables for
        # each cursor that we are using to avoid the creation of them
        # every time that we want to change it
        self.get_window().set_cursor(Gdk.Cursor(Gdk.CursorType.WATCH))

        self.queryresults.connect('updated', self.__query_updated_cb)

    def update_format_combo(self, links):
        self.format_combo.handler_block(self.__format_changed_cb_id)
        self.format_combo.remove_all()
        for key in _MIMETYPES.keys():
            if _MIMETYPES[key] in links.keys():
                self.format_combo.append_item(_MIMETYPES[key], key)
        self.format_combo.set_active(0)
        self.format_combo.handler_unblock(self.__format_changed_cb_id)

    def get_search_terms(self):
        return self._books_toolbar.search_entry.props.text

    def __device_changed_cb(self, mgr):
        logging.debug('Device was added/removed')
        self._refresh_sources(self._books_toolbar)

    def _refresh_sources(self, toolbar):
        toolbar.source_combo.handler_block(toolbar.source_changed_cb_id)

        #TODO: Do not blindly clear this
        toolbar.source_combo.remove_all()

        position = 0
        for key in _SOURCES.keys():
            toolbar.source_combo.append_item(_SOURCES[key], key,
                icon_name='internet-icon')
            _SOURCES_CONFIG[key]['position'] = position
            position = position + 1

        # Add menu for local books
        if len(_SOURCES) > 0:
            toolbar.source_combo.append_separator()
        toolbar.source_combo.append_item('local_books', _('My books'),
                icon_name='activity-journal')

        devices = self._device_manager.get_devices()

        first_device = True
        for device_name in devices:
            device = devices[device_name]
            logging.debug('device %s', device)
            if device['removable']:
                mount_point = device['mount_path']
                label = device['label']
                if label == '' or label is None:
                    capacity = device['size']
                    label = (_('%.2f GB Volume') % (capacity / (1024.0 ** 3)))
                logging.debug('Adding device %s', (label))
                if first_device:
                    toolbar.source_combo.append_separator()
                    first_device = False
                toolbar.source_combo.append_item(mount_point, label)

        toolbar.source_combo.set_active(0)
        toolbar.source_combo.handler_unblock(toolbar.source_changed_cb_id)

    def __format_changed_cb(self, combo):
        self.show_book_data(False)

    def __language_changed_cb(self, combo):
        self.find_books(self.get_search_terms())

    def __search_entry_activate_cb(self, entry):
        self.find_books(self.get_search_terms())

    def __get_book_cb(self, button):
        self.get_book()

    def enable_button(self,  state):
        self._download.props.sensitive = state
        self.format_combo.props.sensitive = state

    def move_up_catalog(self, treeview):
        len_cat = len(self.catalog_history)
        if len_cat == 1:
            return
        else:
            # move a level up the tree
            self.catalog_listview.handler_block(self._catalog_changed_id)
            self.catalog_history.pop()
            len_cat -= 1
            if(len_cat == 1):
                title = self.catalog_history[0]['title']
                self.bt_move_up_catalog.set_label(title)
                self.bt_move_up_catalog.hide_image()
            else:
                title = self.catalog_history[len_cat - 1]['title']
                self.bt_move_up_catalog.set_label(title)
                self.bt_move_up_catalog.show_image()
            self.catalogs = self.catalog_history[len_cat - 1]['catalogs']
            if len(self.catalogs) > 0:
                self.path_iter = {}
                self.categories = []
                for key in self.catalogs.keys():
                    self.categories.append({'text': key, 'dentro': []})
                self.treemodel.clear()
                for p in self.categories:
                    self.path_iter[p['text']] = \
                            self.treemodel.append([p['text']])
            self.catalog_listview.handler_unblock(self._catalog_changed_id)

    def move_down_catalog(self, treeview):
        treestore, coldex = \
                self.catalog_listview.get_selection().get_selected()
        len_cat = len(self.catalog_history)
        if len_cat > 0 and self.catalog_history[len_cat - 1]['catalogs'] == []:
            self.catalog_history.pop()
            len_cat = len(self.catalog_history)

        # README: when the Activity starts by default there is nothing
        # selected and this signal is called, so we have to avoid this
        # 'append' because it fails
        if coldex is not None:
            self.catalog_history.append(
                    {'title': treestore.get_value(coldex, 0), 'catalogs': []})
            self.__switch_catalog_cb(treestore.get_value(coldex, 0))

    def _sort_logfile(self, treemodel, itera, iterb):
        a = treemodel.get_value(itera, 0)
        b = treemodel.get_value(iterb, 0)
        if a == None or b == None:
            return 0
        a = a.lower()
        b = b.lower()
        if a > b:
            return 1
        if a < b:
            return -1
        return 0

    def __toggle_cats_cb(self, button):
        if button.get_active():
            self.tree_scroller.show_all()
            self.separa.show()
        else:
            self.tree_scroller.hide()
            self.separa.hide()

    def _create_controls(self):
        self._download_content_length = 0
        self._download_content_type = None

        self.msg_label = Gtk.Label()

        self.list_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        # Catalogs treeview
        self.catalog_listview = Gtk.TreeView()
        self.catalog_listview.headers_clickble = True
        self.catalog_listview.hover_expand = True
        self.catalog_listview.rules_hint = True
        self._catalog_changed_id = self.catalog_listview.connect(
                'cursor-changed', self.move_down_catalog)
        self.catalog_listview.set_enable_search(False)

        self.treemodel = Gtk.ListStore(str)
        self.treemodel.set_sort_column_id(0, Gtk.SortType.ASCENDING)
        self.catalog_listview.set_model(self.treemodel)

        renderer = Gtk.CellRendererText()
        renderer.set_property('wrap-mode', Pango.WrapMode.WORD)
        self.treecol = Gtk.TreeViewColumn(_('Catalogs'), renderer, text=0)
        self.treecol.set_property('clickable', True)
        self.treecol.connect('clicked', self.move_up_catalog)
        self.catalog_listview.append_column(self.treecol)
        self.bt_move_up_catalog = ButtonWithImage(_('Catalogs'))
        self.bt_move_up_catalog.hide_image()
        self.treecol.set_widget(self.bt_move_up_catalog)

        self.load_source_catalogs()

        self.tree_scroller = Gtk.ScrolledWindow(hadjustment=None,
                vadjustment=None)
        self.tree_scroller.set_policy(Gtk.PolicyType.NEVER,
                Gtk.PolicyType.AUTOMATIC)
        self.tree_scroller.add(self.catalog_listview)
        self.list_box.pack_start(self.tree_scroller, expand=False,
                fill=False, padding=0)
        self.separa = Gtk.VSeparator()
        self.list_box.pack_start(self.separa, expand=False,
                fill=False, padding=0)

        # books listview
        self.listview = ListView(self._lang_code_handler)
        self.selection_cb_id = self.listview.connect('selection-changed',
                                                     self.selection_cb)
        self.listview.set_enable_search(False)

        self.list_scroller = Gtk.ScrolledWindow(hadjustment=None,
                vadjustment=None)
        self.list_scroller.set_policy(Gtk.PolicyType.AUTOMATIC,
                Gtk.PolicyType.AUTOMATIC)
        vadjustment = self.list_scroller.get_vadjustment()
        vadjustment.connect('value-changed',
                self.__vadjustment_value_changed_cb)
        self.list_scroller.add(self.listview)
        self.list_box.pack_start(self.list_scroller, expand=True,
                fill=True, padding=0)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.NEVER,
                Gtk.PolicyType.AUTOMATIC)
        self.scrolled.props.shadow_type = Gtk.ShadowType.ETCHED_OUT
        self.textview = Gtk.TextView()
        self.textview.set_editable(False)
        self.textview.set_cursor_visible(False)
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
        self.textview.set_justification(Gtk.Justification.LEFT)
        self.textview.set_left_margin(60)
        self.textview.set_right_margin(60)
        self.scrolled.add(self.textview)
        self.list_box.show_all()
        self.separa.hide()
        self.tree_scroller.hide()

        vbox_download = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        hbox_format = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        format_label = Gtk.Label(label=_('Format:'))
        self.format_combo = ComboBox()
        for key in _MIMETYPES.keys():
            self.format_combo.append_item(_MIMETYPES[key], key)
        self.format_combo.set_active(0)
        self.format_combo.props.sensitive = False
        self.__format_changed_cb_id = \
                self.format_combo.connect('changed', self.__format_changed_cb)

        hbox_format.pack_start(format_label, False, False, 10)
        hbox_format.pack_start(self.format_combo, False, False, 10)
        vbox_download.pack_start(hbox_format, False, False, 10)

        self._download = Gtk.Button(_('Get Book'))
        self._download.set_image(Icon(icon_name='data-download'))
        self._download.props.sensitive = False
        self._download.connect('clicked', self.__get_book_cb)
        vbox_download.pack_start(self._download, False, False, 10)

        self.progressbox = Gtk.Box(spacing=20,
                orientation=Gtk.Orientation.HORIZONTAL)
        self.progressbar = Gtk.ProgressBar()
        self.progressbar.set_fraction(0.0)
        self.progressbox.pack_start(self.progressbar, expand=True, fill=True,
                padding=0)
        self.cancel_btn = Gtk.Button(stock=Gtk.STOCK_CANCEL)
        self.cancel_btn.set_image(Icon(icon_name='dialog-cancel'))
        self.cancel_btn.connect('clicked', self.__cancel_btn_clicked_cb)
        self.progressbox.pack_start(self.cancel_btn, expand=False,
                fill=False, padding=0)
        vbox_download.pack_start(self.progressbox, False, False, 10)

        bottom_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        if self.show_images:
            self.__image_downloader = None
            self.image = Gtk.Image()
            self.add_default_image()
            bottom_hbox.pack_start(self.image, False, False, 10)
        bottom_hbox.pack_start(self.scrolled, True, True, 10)
        bottom_hbox.pack_start(vbox_download, False, False, 10)
        bottom_hbox.show_all()

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        vbox.pack_start(self.msg_label, False, False, 10)
        vbox.pack_start(self.list_box, True, True, 0)
        vbox.pack_start(bottom_hbox, False, False, 10)
        self.set_canvas(vbox)
        self.listview.show()
        vbox.show()
        self.list_scroller.show()
        self.progress_hide()
        self.show_message(
                _('Enter words from the Author or Title to begin search.'))

        self._books_toolbar.search_entry.grab_focus()
        if len(self.catalogs) > 0:
            self.bt_catalogs.set_active(True)

    def progress_hide(self):
        self.clear_downloaded_bytes()
        self.progressbar.set_sensitive(False)
        self.cancel_btn.set_sensitive(False)

    def progress_show(self):
        self.progressbar.set_sensitive(True)
        self.cancel_btn.set_sensitive(True)

    def filter_catalogs_by_source(self):
        self.catalogs = {}
        for catalog_key in self.catalogs_configuration:
            catalog = self.catalogs_configuration[catalog_key]
            if catalog['source'] == self.source:
                self.catalogs[catalog_key] = catalog

    def load_source_catalogs(self):
        self.filter_catalogs_by_source()

        if len(self.catalogs) > 0:
            self.categories = []
            self.path_iter = {}
            self.catalog_history = []
            self.catalog_history.append({'title': _('Catalogs'),
                'catalogs': self.catalogs})
            for key in self.catalogs.keys():
                self.categories.append({'text': key, 'dentro': []})
            self.treemodel.clear()

            for p in self.categories:
                self.path_iter[p['text']] = self.treemodel.append([p['text']])

    def can_close(self):
        if self.queryresults is not None:
            self.queryresults.cancel()
            self.queryresults = None
        return True

    def selection_cb(self, widget):
        selected_book = self.listview.get_selected_book()
        if self.source == 'local_books':
            if selected_book:
                self.selected_book = selected_book
                self._download.hide()
                self.show_book_data()
                self._object_id = selected_book.get_object_id()
                self._show_journal_alert(_('Selected book'),
                        self.selected_title)
        else:
            self.clear_downloaded_bytes()
            if selected_book:
                self.update_format_combo(selected_book.get_types())
                self.selected_book = selected_book
                self._download.show()
                self.show_book_data()

    def show_message(self, text):
        self.msg_label.set_text(text)
        self.msg_label.show()

    def hide_message(self):
        self.msg_label.hide()

    def show_book_data(self, load_image=True):
        self.selected_title = self.selected_book.get_title()
        book_data = _('Title:\t\t') + self.selected_title + '\n'
        self.selected_author = self.selected_book.get_author()
        book_data += _('Author:\t\t') + self.selected_author + '\n'
        self.selected_publisher = self.selected_book.get_publisher()
        self.selected_summary = self.selected_book.get_summary()
        if (self.selected_summary is not 'Unknown'):
            book_data += _('Summary:\t') + self.selected_summary + '\n'
        self.selected_language_code = self.selected_book.get_language()
        if self.selected_language_code != '':
            try:
                self.selected_language = \
                    self._lang_code_handler.get_full_language_name(
                        self.selected_book.get_language())
            except:
                self.selected_language = self.selected_book.get_language()
            book_data += _('Language:\t') + self.selected_language + '\n'
        book_data += _('Publisher:\t') + self.selected_publisher + '\n'
        textbuffer = self.textview.get_buffer()
        textbuffer.set_text('\n' + book_data)
        self.enable_button(True)

        # Cover Image
        self.exist_cover_image = False
        if self.show_images and load_image:
            if self.source == 'local_books':
                cover_image_buffer = self.get_journal_entry_cover_image(
                        self.selected_book.get_object_id())
                if (cover_image_buffer):
                    self.add_image_buffer(
                        self.get_pixbuf_from_buffer(cover_image_buffer))
                else:
                    self.add_default_image()
            else:
                url_image = self.selected_book.get_image_url()
                self.add_default_image()
                if url_image:
                    self.download_image(url_image.values()[0])

    def get_pixbuf_from_buffer(self, image_buffer):
        """Buffer To Pixbuf"""
        pixbuf_loader = GdkPixbuf.PixbufLoader()
        pixbuf_loader.write(image_buffer)
        pixbuf_loader.close()
        pixbuf = pixbuf_loader.get_pixbuf()
        return pixbuf

    def get_journal_entry_cover_image(self, object_id):
        ds_object = datastore.get(object_id)
        if 'cover_image' in ds_object.metadata:
            cover_data = ds_object.metadata['cover_image']
            return base64.b64decode(cover_data)
        elif 'preview' in ds_object.metadata:
            return ds_object.metadata['preview']
        else:
            return ""

    def download_image(self,  url):
        self._inhibit_suspend()
        self.progress_show()
        if self.__image_downloader is not None:
            self.__image_downloader.stop()
        self.__image_downloader = opds.FileDownloader(url, self.get_path())
        self.__image_downloader.connect('updated', self.__image_updated_cb)
        self.__image_downloader.connect('progress', self.__image_progress_cb)

    def __image_updated_cb(self, downloader, path, content_type):
        if path is not None:
            self.add_image(path)
            self.exist_cover_image = True
            os.remove(path)
        else:
            self.add_default_image()
        self.__image_downloader = None
        GObject.timeout_add(500, self.progress_hide)
        self._allow_suspend()

    def __image_progress_cb(self, downloader, progress):
        self.progressbar.set_fraction(progress)
        while Gtk.events_pending():
            Gtk.main_iteration()

    def add_default_image(self):
        file_path = os.path.join(activity.get_bundle_path(),
                'generic_cover.png')
        self.add_image(file_path)

    def add_image(self, file_path):
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(file_path)
        self.add_image_buffer(pixbuf)

    def add_image_buffer(self, pixbuf):
        image_height = int(Gdk.Screen.height() / 4)
        image_width = image_height / 3 * 2
        width, height = pixbuf.get_width(), pixbuf.get_height()
        scale = 1
        if (width > image_width) or (height > image_height):
            scale_x = image_width / float(width)
            scale_y = image_height / float(height)
            scale = min(scale_x, scale_y)

        pixbuf2 = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,
                pixbuf.get_has_alpha(), pixbuf.get_bits_per_sample(),
                image_width, image_height)

        pixbuf2.fill(style.COLOR_PANEL_GREY.get_int())

        margin_x = int((image_width - (width * scale)) / 2)
        margin_y = int((image_height - (height * scale)) / 2)

        pixbuf.scale(pixbuf2, margin_x, margin_y,
                image_width - (margin_x * 2), image_height - (margin_y * 2),
                margin_x, margin_y, scale, scale,
                GdkPixbuf.InterpType.BILINEAR)

        self.image.set_from_pixbuf(pixbuf2)

    def get_query_language(self):
        query_language = None
        if len(self.languages) > 0:
            query_language = self._books_toolbar.language_combo.props.value
        return query_language

    def find_books(self, search_text=''):
        self._inhibit_suspend()
        self.source = self._books_toolbar.source_combo.props.value

        query_language = self.get_query_language()

        self.enable_button(False)
        self.clear_downloaded_bytes()
        self.book_selected = False
        self.listview.handler_block(self.selection_cb_id)
        self.listview.clear()
        self.listview.handler_unblock(self.selection_cb_id)

        if self.queryresults is not None:
            self.queryresults.cancel()
            self.queryresults = None

        if self.source == 'local_books':
            self.listview.populate_with_books(
                    self.get_entrys_info(search_text))
        else:
            if search_text is None:
                return
            elif len(search_text) < 3:
                self.show_message(_('You must enter at least 3 letters.'))
                self._books_toolbar.search_entry.grab_focus()
                return
            if self.source == 'Internet Archive':
                self.queryresults = \
                        opds.InternetArchiveQueryResult(search_text,
                                                        self.get_path())
            elif self.source in _SOURCES_CONFIG:
                repo_configuration = _SOURCES_CONFIG[self.source]
                self.queryresults = opds.RemoteQueryResult(repo_configuration,
                        search_text, query_language)
            else:
                self.queryresults = opds.LocalVolumeQueryResult(self.source,
                        search_text, query_language)

            self.show_message(_('Performing lookup, please wait...'))
            self.get_window().set_cursor(Gdk.Cursor(Gdk.CursorType.WATCH))
            self.queryresults.connect('updated', self.__query_updated_cb)

    def __query_updated_cb(self, query, midway):
        self.listview.populate(self.queryresults)
        if hasattr(self.queryresults, '_feedobj') and \
           'bozo_exception' in self.queryresults._feedobj:
            # something went wrong and we have to inform about this
            bozo_exception = self.queryresults._feedobj.bozo_exception
            if isinstance(bozo_exception, urllib2.URLError):
                if isinstance(bozo_exception.reason, socket.gaierror):
                    if bozo_exception.reason.errno == -2:
                        self.show_message(_('Could not reach the server. '
                            'Maybe you are not connected to the network'))
                        self.window.set_cursor(None)
                        return
            self.show_message(_('There was an error downloading the list.'))
        elif (len(self.queryresults.get_catalog_list()) > 0):
            self.show_message(_('New catalog list %s was found') \
                % self.queryresults._configuration["name"])
            self.catalogs_updated(query, midway)
        elif len(self.queryresults) == 0:
            self.show_message(_('Sorry, no books could be found.'))
        if not midway and len(self.queryresults) > 0:
            self.hide_message()
            query_language = self.get_query_language()
            if query_language != 'all' and query_language != 'en':
                # the bookserver send english books if there are not books in
                # the requested language
                only_english = True
                for book in self.queryresults.get_book_list():
                    if book.get_language() == query_language:
                        only_english = False
                        break
                if only_english:
                    self.show_message(
                            _('Sorry, we only found english books.'))
        self.get_window().set_cursor(None)
        self._allow_suspend()

    def catalogs_updated(self, query, midway):
        self.catalogs = {}
        for catalog_item in self.queryresults.get_catalog_list():
            logging.debug('Add catalog %s', catalog_item.get_title())
            catalog_config = {}
            download_link = ''
            download_links = catalog_item.get_download_links()
            for link in download_links.keys():
                download_link = download_links[link]
                break
            catalog_config['query_uri'] = download_link
            catalog_config['opds_cover'] = \
                catalog_item._configuration['opds_cover']
            catalog_config['source'] = catalog_item._configuration['source']
            source_config = _SOURCES_CONFIG[catalog_config['source']]
            catalog_config['name'] = catalog_item.get_title()
            catalog_config['summary_field'] = \
                catalog_item._configuration['summary_field']
            if catalog_item.get_title() in source_config['blacklist']:
                logging.debug('Catalog "%s" is in blacklist',
                    catalog_item.get_title())
            else:
                self.catalogs[catalog_item.get_title().strip()] = \
                        catalog_config

        if len(self.catalogs) > 0:
            len_cat = len(self.catalog_history)
            self.catalog_history[len_cat - 1]['catalogs'] = self.catalogs
            self.path_iter = {}
            self.categories = []
            for key in self.catalogs.keys():
                self.categories.append({'text': key, 'dentro': []})
            self.treemodel.clear()
            for p in self.categories:
                self.path_iter[p['text']] = \
                        self.treemodel.append([p['text']])

            title = self.catalog_history[len_cat - 1]['title']
            self.bt_move_up_catalog.set_label(title)
            self.bt_move_up_catalog.show_image()

        else:
            self.catalog_history.pop()

    def __source_changed_cb(self, widget):
        search_terms = self.get_search_terms()
        if search_terms == '':
            self.find_books(None)
        else:
            self.find_books(search_terms)
        # enable/disable catalogs button if configuration is available
        self.source = self._books_toolbar.source_combo.props.value

        # Get catalogs for this source
        self.load_source_catalogs()

        if len(self.catalogs) > 0:
            self.bt_catalogs.show()
            self.bt_catalogs.set_active(True)
        else:
            self.bt_catalogs.set_active(False)
            self.bt_catalogs.hide()

    def __vadjustment_value_changed_cb(self, vadjustment):

        if not self.queryresults.is_ready():
            return
        try:
            # Use various tricks to update resultset as user scrolls down
            if ((vadjustment.props.upper - vadjustment.props.lower) > 1000 \
                and (vadjustment.props.upper - vadjustment.props.value - \
                vadjustment.props.page_size) / (vadjustment.props.upper - \
                vadjustment.props.lower) < 0.3) or ((vadjustment.props.upper \
                - vadjustment.props.value
                - vadjustment.props.page_size) < 200):
                if self.queryresults.has_next():
                    self.queryresults.update_with_next()
        finally:
            return

    def __cancel_btn_clicked_cb(self, btn):
        if self.__image_downloader is not None:
            self.__image_downloader.stop()

        if self.__book_downloader is not None:
            self.__book_downloader.stop()

        self.progress_hide()
        self.enable_button(True)
        self.listview.props.sensitive = True
        self._books_toolbar.search_entry.set_sensitive(True)
        self._allow_suspend()

    def get_book(self):
        self.enable_button(False)
        self.clear_downloaded_bytes()
        self.progress_show()
        if self.source != 'local_books':
            self.selected_book.get_download_links(self.format_combo.props.value,
                                                  self.download_book,
                                                  self.get_path())

    def download_book(self,  url):
        logging.error('DOWNLOAD BOOK %s', url)
        self._inhibit_suspend()
        self.listview.props.sensitive = False
        self._books_toolbar.search_entry.set_sensitive(False)
        self.__book_downloader = opds.FileDownloader(url, self.get_path())
        self.__book_downloader.connect('updated', self.__book_updated_cb)
        self.__book_downloader.connect('progress', self.__book_progress_cb)

    def __book_updated_cb(self, downloader, path, content_type):
        self._books_toolbar.search_entry.set_sensitive(True)
        self.listview.props.sensitive = True
        self._allow_suspend()
        GObject.timeout_add(500, self.progress_hide)
        self.enable_button(True)
        self.__book_downloader = None

        if path is None:
            self._show_error_alert(_('Error: Could not download %s. ' +
                    'The path in the catalog seems to be incorrect.') %
                    self.selected_title)
            return

        if os.stat(path).st_size == 0:
            self._show_error_alert(_('Error: Could not download %s. ' +
                    'The other end sent an empty file.') %
                    self.selected_title)
            return

        if content_type.startswith('text/html'):
            self._show_error_alert(_('Error: Could not download %s. ' +
                    'The other end sent text/html instead of a book.') %
                    self.selected_title)
            return

        self.process_downloaded_book(path)

    def __book_progress_cb(self, downloader, progress):
        self.progressbar.set_fraction(progress)
        while Gtk.events_pending():
            Gtk.main_iteration()

    def clear_downloaded_bytes(self):
        self.progressbar.set_fraction(0.0)

    def process_downloaded_book(self, path):
        logging.debug("Got document %s", path)
        self.create_journal_entry(path)
        self._getter = None
        self._allow_suspend()

    def create_journal_entry(self, path):
        journal_entry = datastore.create()
        journal_title = self.selected_title
        if self.selected_author != '':
            journal_title = journal_title + ', by ' + self.selected_author
        journal_entry.metadata['title'] = journal_title
        journal_entry.metadata['title_set_by_user'] = '1'
        journal_entry.metadata['keep'] = '0'
        journal_entry.metadata['mime_type'] = \
                self.format_combo.props.value
        # Fix fake mime type for black&white pdfs
        if journal_entry.metadata['mime_type'] == _MIMETYPES['PDF BW']:
            journal_entry.metadata['mime_type'] = _MIMETYPES['PDF']

        journal_entry.metadata['buddies'] = ''
        journal_entry.metadata['icon-color'] = profile.get_color().to_string()
        textbuffer = self.textview.get_buffer()
        journal_entry.metadata['description'] = \
            textbuffer.get_text(textbuffer.get_start_iter(),
                                textbuffer.get_end_iter(), True)
        if self.exist_cover_image:
            image_buffer = self._get_preview_image_buffer()
            journal_entry.metadata['preview'] = dbus.ByteArray(image_buffer)
            image_buffer = self._get_cover_image_buffer()
            journal_entry.metadata['cover_image'] = \
                dbus.ByteArray(base64.b64encode(image_buffer))
        else:
            journal_entry.metadata['cover_image'] = ""

        journal_entry.metadata['tags'] = self.source
        journal_entry.metadata['source'] = self.source
        journal_entry.metadata['author'] = self.selected_author
        journal_entry.metadata['publisher'] = self.selected_publisher
        journal_entry.metadata['summary'] = self.selected_summary
        journal_entry.metadata['language'] = self.selected_language_code

        journal_entry.file_path = path
        datastore.write(journal_entry)
        os.remove(path)
        self.progress_hide()
        self._object_id = journal_entry.object_id
        self._show_journal_alert(_('Download completed'), self.selected_title)

    def _show_journal_alert(self, title, msg):
        _stop_alert = Alert()
        _stop_alert.props.title = title
        _stop_alert.props.msg = msg

        if _HAS_BUNDLE_LAUNCHER:
                bundle = get_bundle(object_id=self._object_id)

        if bundle is not None:
            icon = Icon(file=bundle.get_icon())
            label = _('Open with %s') % bundle.get_name()
            _stop_alert.add_button(Gtk.ResponseType.ACCEPT, label, icon)
        else:
            icon = Icon(icon_name='zoom-activity')
            label = _('Show in Journal')
            _stop_alert.add_button(Gtk.ResponseType.APPLY, label, icon)
        icon.show()

        ok_icon = Icon(icon_name='dialog-ok')
        _stop_alert.add_button(Gtk.ResponseType.OK, _('Ok'), ok_icon)
        ok_icon.show()
        # Remove other alerts
        for alert in self._alerts:
            self.remove_alert(alert)

        self.add_alert(_stop_alert)
        _stop_alert.connect('response', self.__stop_response_cb)
        _stop_alert.show()

    def __stop_response_cb(self, alert, response_id):
        if response_id is Gtk.ResponseType.APPLY:
            activity.show_object_in_journal(self._object_id)
        elif response_id is Gtk.ResponseType.ACCEPT:
            launch_bundle(object_id=self._object_id)
        self.remove_alert(alert)

    def _get_preview_image_buffer(self):
        preview_width, preview_height = style.zoom(300), style.zoom(225)

        pixbuf = self.image.get_pixbuf()
        width, height = pixbuf.get_width(), pixbuf.get_height()

        scale = 1
        if (width > preview_width) or (height > preview_height):
            scale_x = preview_width / float(width)
            scale_y = preview_height / float(height)
            scale = min(scale_x, scale_y)

        pixbuf2 = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,
                pixbuf.get_has_alpha(), pixbuf.get_bits_per_sample(),
                preview_width, preview_height)
        pixbuf2.fill(style.COLOR_WHITE.get_int())

        margin_x = int((preview_width - (width * scale)) / 2)
        margin_y = int((preview_height - (height * scale)) / 2)

        pixbuf.scale(pixbuf2, margin_x, margin_y,
                preview_width - (margin_x * 2),
                preview_height - (margin_y * 2),
                margin_x, margin_y, scale, scale,
                GdkPixbuf.InterpType.BILINEAR)

        succes, data = pixbuf2.save_to_bufferv('png', [], [])
        return data

    def _get_cover_image_buffer(self):
        pixbuf = self.image.get_pixbuf()
        succes, data = pixbuf.save_to_bufferv('png', [], [])
        return data

    def _show_error_alert(self, title, text=None):
        alert = NotifyAlert(timeout=20)
        alert.props.title = title
        alert.props.msg = text
        self.add_alert(alert)
        alert.connect('response', self._alert_cancel_cb)
        alert.show()

    def _alert_cancel_cb(self, alert, response_id):
        self.remove_alert(alert)
        self.textview.grab_focus()

    def get_entrys_info(self, query):
        books = []
        for key in _MIMETYPES.keys():
            books.extend(self.get_entry_info_format(query, _MIMETYPES[key]))
        return books

    def get_entry_info_format(self, query, mime):
        books = []
        if query is not None and len(query) > 0:
            ds_objects, num_objects = datastore.find(
                    {'mime_type': '%s' % mime,
                    'query': '*%s*' % query})
        else:
            ds_objects, num_objects = datastore.find(
                    {'mime_type': '%s' % mime})

        logging.error('Local search %d books found %s format', num_objects,
                    mime)
        for i in range(0, num_objects):
            entry = {}
            entry['title'] = ds_objects[i].metadata['title']
            entry['mime'] = ds_objects[i].metadata['mime_type']
            entry['object_id'] = ds_objects[i].object_id

            if 'author' in ds_objects[i].metadata:
                entry['author'] = ds_objects[i].metadata['author']
            else:
                entry['author'] = ''

            if 'publisher' in ds_objects[i].metadata:
                entry['dcterms_publisher'] = \
                    ds_objects[i].metadata['publisher']
            else:
                entry['dcterms_publisher'] = ''

            if 'language' in ds_objects[i].metadata:
                entry['dcterms_language'] = \
                    ds_objects[i].metadata['language']
            else:
                entry['dcterms_language'] = ''

            if 'source' in ds_objects[i].metadata:
                entry['source'] = \
                    ds_objects[i].metadata['source']
            else:
                entry['source'] = ''

            if entry['source'] in _SOURCES_CONFIG:
                repo_configuration = _SOURCES_CONFIG[entry['source']]
                summary_field = repo_configuration['summary_field']
                if 'summary' in ds_objects[i].metadata:
                    entry[summary_field] = ds_objects[i].metadata['summary']
                else:
                    entry[summary_field] = ''
            else:
                repo_configuration = None
            books.append(opds.Book(repo_configuration, entry, ''))
        return books

    def close(self,  skip_save=False):
        "Override the close method so we don't try to create a Journal entry."
        activity.Activity.close(self,  True)

    def save(self):
        pass


class ButtonWithImage(Gtk.Button):

    def __init__(self, label_text):
        GObject.GObject.__init__(self,)
        self.icon_move_up = Icon(icon_name='go-up')
        # self.remove(self.get_children()[0])
        self.hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.add(self.hbox)
        self.hbox.add(self.icon_move_up)
        self.label = Gtk.Label(label=label_text)
        self.hbox.add(self.label)
        self.show_all()

    def hide_image(self):
        self.icon_move_up.hide()

    def show_image(self):
        self.icon_move_up.show()

    def set_label(self, text):
        self.label.set_text(text)
