#!/usr/bin/env python

import extListview, gobject, gtk, gtk.gdk, pango, sys
import opds

def onListModified():
    print 'Modified!'

def onColumnVisibilityChanged(list, colTitle, visible):
    print 'Visibility of', colTitle, 'is now', visible

# Setup the main window
window = gtk.Window(gtk.WINDOW_TOPLEVEL)
window.connect('delete_event', gtk.main_quit)
#window.set_default_size(450, 300)

# Setup the scrolled window
scrolledwin = gtk.ScrolledWindow()
scrolledwin.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
box = gtk.VBox()
window.add(box)
box.pack_start(scrolledwin)
box.set_homogeneous(False)

# Create the two renderers used in the listview
txtRdr    = gtk.CellRendererText()
txtRdr.props.wrap_mode = pango.WRAP_WORD
txtRdr.props.wrap_width = 500
pixbufRdr = gtk.CellRendererPixbuf()

# The fields in a row of the listview
(
    ROW_TIT,
    ROW_AUT,
    ROW_PUB,
    ROW_LANG,
    ROW_DATE
) = range(5)

# Setup the columns
#  Part 1 is the title of the column. If None, the column is not visible at all.
#  Part 2 is a list of tuple (CellRenderer, ValueType) to be put in that column.
#  Part 3 is the tuple with all the fields used when sorting on that column: first sort on the first field, then on the second...
#  Part 4 is a boolean that specifies whether the column is expanded.
#  Part 5 is a boolean giving the initial visibility of the column. This can be changed by the user by right-clicking on the column headers.
columns =  (('Title',  [(txtRdr, gobject.TYPE_STRING)],                           (ROW_TIT,),                                    True,  True),
           ('Author', [(txtRdr, gobject.TYPE_STRING)],                           (ROW_AUT, ROW_TIT), True,  True),
           ('Publisher',  [(txtRdr, gobject.TYPE_STRING)],                           (ROW_AUT, ROW_TIT),                   True,  True),
           ('Language',  [(txtRdr, gobject.TYPE_STRING)],                           (ROW_AUT, ROW_TIT), True,  False),
           (None,     [(None, gobject.TYPE_STRING)],                             (None,),                                       False, False))

listview = extListview.ExtListView(columns, sortable=True, useMarkup=False, canShowHideColumns=True)
listview.enableDNDReordering()
listview.connect('extlistview-modified', lambda *args: onListModified())
listview.connect('extlistview-column-visibility-changed', onColumnVisibilityChanged)
scrolledwin.add(listview)

# Buttons
#buttons = gtk.HButtonBox()
#box.pack_start(buttons, False)

#shuffleBtn = gtk.Button('Shuffle')
#shuffleBtn.connect('clicked', lambda btn: listview.shuffle())
#buttons.add(shuffleBtn)

# Some arbitrary data to put in the listview
#icon = listview.render_icon(gtk.STOCK_MEDIA_PLAY, gtk.ICON_SIZE_MENU)

rows = []

searchresults = opds.FeedBooksQueryResult('Jules Verne')

for book in searchresults.get_book_list():
    try:
        rows.append([book.get_title(), book.get_author(), book.get_publisher(), 'English', book.get_published_year()])
        #rows.append([entry['title'], entry['author'], entry['publisher'], entry['language'], entry['published']])
    except:
        print sys.exc_info()

listview.insertRows(rows)

# Let's go
window.show_all()
gtk.main()
