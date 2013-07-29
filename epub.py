import os, sys
from ConfigParser import ConfigParser
import glob
import re
import shutil
import string
import StringIO
import subprocess
import tempfile
import time
import urllib
import zipfile

from lxml import etree

SOURCE_DIRPATH = "source"
HERE = os.path.dirname(__file__)
TEMPLATE_DIRPATH = os.path.join(HERE, "template")

XHTML_DOCUMENT = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html
    PUBLIC "-//W3C//DTD XHTML 1.1//EN"
    "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd"
>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
  <head>
  </head>
  <body>
  </body>
</html>
'''
XHTML_DOCTYPE = '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'

def as_code(name):
    return "-".join(name.lower().split())

def write_xml (xml, filepath, **kwargs):
    with open(filepath, "wb") as f:
        f.write(etree.tostring(xml, pretty_print=True, xml_declaration=True, encoding="utf-8", **kwargs))

opf_namespace = "http://www.idpf.org/2007/opf"
OPF = "{%s}" % opf_namespace
dc_namespace = "http://purl.org/dc/elements/1.1/"
DC = "{%s}" % dc_namespace
daisy_namespace = "http://www.daisy.org/z3986/2005/ncx/"
DAISY = "{%s}" % daisy_namespace

def fill_in_toc(xml, title=None, author=None, uid=None, names=None, headings=None):
  """Fill in missing pieces of the toc.ncx
  """
  ncx = xml.getroot()
  if uid:
    head = ncx.find(DAISY + "head")
    meta_uid = head.find('./' + DAISY + 'meta[@name="dtb:uid"]')
    meta_uid.set("content", uid)
  if title:
    docTitle = ncx.find(DAISY + "docTitle")
    text = docTitle.find(DAISY + "text")
    text.text = title
  if author:
    docAuthor = ncx.find(DAISY + "docAuthor")
    text = docAuthor.find(DAISY + "text")
    text.text = author

  #
  # If a list of names is provided, clear the navMap and add the
  # names in the order provided.
  #
  if names:
    #
    # Remove any existing navMap
    #
    navMap = ncx.find(DAISY + "navMap")
    for c in navMap.iterchildren():
      navMap.remove(c)

    index = 1
    other_names = set(['front', 'toc'])
    content_names = set(name for name in names if name not in other_names)
    for name in names:
        if name in other_names:
            #
            # FIXME: The intention is to do something fancy
            # with title pages, TOCs etc. For now though, just
            # ignore them.
            #
            continue

        navPoint = etree.SubElement(navMap, "navPoint", id="chapter-%d" % index, playOrder="%d" % index)
        navLabel = etree.SubElement(navPoint, "navLabel")
        etree.SubElement(navLabel, "text").text = name
        etree.SubElement(navPoint, "content", src="content/%s.xhtml" % name)
        index += 1

        if headings:
            document_headings = headings.get(name)
            if document_headings:
                for id, text in document_headings:
                    subNavPoint = etree.SubElement(navPoint, "navPoint", id=id, playOrder="%d" % index)
                    subNavLabel = etree.SubElement(subNavPoint, "navLabel")
                    etree.SubElement(subNavLabel, "text").text = text
                    etree.SubElement(subNavPoint, "content", src="content/%s.xhtml#%s" % (name, id))
                    index += 1


  return xml


def fill_in_content(xml, title=None, author=None, uid=None, names=None, headings=None):
    """Fill in missing pieces of the content.opf
    """
    package = xml.getroot()
    if uid:
        package.set("unique-identifier", uid)

    metadata = package.find(OPF + "metadata")
    if title:
        metadata_title = metadata.find(DC + "title")
        metadata_title.text = title
    if author:
        metadata_creator = metadata.find(DC + "creator")
        metadata_creator.set(OPF + "file-as", author)
        metadata_creator.set(OPF + "role", "aut")
        metadata_creator.text = author
    if uid:
        metadata_uid = metadata.find(DC + "identifier")
        metadata_uid.set("id", uid)
        metadata_uid.text = "book://%s" % as_code(title)

    #
    # If a list of names is provided, clear the manifest and spine
    # elements, seed with the toc and add the names in the order provided.
    #
    if names:
        manifest = package.find(OPF + "manifest")
        for c in manifest.iterchildren():
            manifest.remove(c)
        toc = etree.SubElement(manifest, "item", id="ncx", href="toc.ncx")
        toc.set("media-type", "application/x-dtbncx+xml")
        spine = package.find(OPF + "spine")
        for c in spine.iterchildren():
            spine.remove(c)
        for name in names:
            if name in ["front"]:
                continue
            manifest_item = etree.SubElement(manifest, "item", id=as_code(name), href="content/%s.xhtml" % name)
            manifest_item.set("media-type", "application/xhtml+xml")
            etree.SubElement(spine, "itemref", idref=as_code(name))

    return xml

def convert_to_xhtml(filepath, dirpath):
    #
    # By default the etree parser will open the file
    # in binary mode, leaving the windows-style \r\n linefeeds
    # intact. The end result is #&13 turds all over the XHTML.
    #
    if filepath.startswith("http://"):
        f = urllib.urlopen(filepath)
    else:
        f = open(filepath, "rb")
    try:
        text = f.read()
    finally:
        f.close()
    text = text.replace("\r\n", "\n")
    html = etree.parse(StringIO.StringIO(text), etree.HTMLParser())
    #
    # Strip tags which won't be needed
    #
    for br in html.iter("br"):
        previous = br.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + " "
        else:
            br.tail = " " + (br.tail or "")

    #
    # A little bit dangerous but... the header block on papalencycicals.net uses
    # divs whose generated ids all start with "DMSMenu"
    #
    for div in list(html.iter("div")):
        id = div.get("id")
        if id and id.startswith("DMSMenu"):
            div.getparent().remove(div)

    etree.strip_tags(html, "span", "script", "center", "style", "br", "a", "table", "img", "tr", "td", "font", "div")
    #
    # Remove tag-level styles and alignments
    #
    etree.strip_attributes(html, "class", "style", "align")
    #
    # Remove paragraphs which are completely empty
    #
    for p in html.iter("p"):
        if not list(p) and not (p.text or "").strip() and not (p.tail or "").strip():
            p.getparent().remove(p)

    html_body = html.find("body")
    html_body.attrib.clear()

    xml = etree.XML(XHTML_DOCUMENT)
    xml_body = xml.find("{%s}body" % xml.nsmap[None])
    xml_body.getparent().replace(xml_body, html_body)

    base, ext = os.path.splitext(os.path.basename(filepath))
    output_filepath = os.path.join(dirpath, "%s.xhtml" % base)
    with open(output_filepath, "wb") as f:
        f.write(
            etree.tostring(
                xml,
                pretty_print=True,
                xml_declaration=True,
                encoding="utf-8"
            )
        )

class EPub(object):

    config_filename = "epub.ini"
    opf_namespace = "http://www.idpf.org/2007/opf"
    opf = "{%s}" % opf_namespace
    dc_namespace = "http://purl.org/dc/elements/1.1/"
    dc = "{%s}" % dc_namespace

    def __init__(self):
        self.document_headings = {}

    def _set_from_config(self, config_filepath):
        self.title = self.author = self.uid = self.headings = None

        self.config = ConfigParser()
        self.config.read([config_filepath])
        if self.config.has_section("metadata"):
            self.title = self.config.get("metadata", "title")
            self.author = self.config.get("metadata", "author")
            self.uid = self.config.get("metadata", "uid")
        if self.config.has_section("headings"):
            self.headings = self.config.items("headings")

    def _generate_skeleton(self, title, author, uid, from_dirpath):
        #
        # Create the root directory for the epub
        #
        print "Create the root directory"
        os.mkdir(title)

        #
        # Create the epub.ini file, seeded with the title, author & uid
        #
        print "Create ini:", self.config_filename
        config_filepath = os.path.join(from_dirpath, self.config_filename)
        self.config = config = ConfigParser()
        config.read([config_filepath])
        if not config.has_section("metadata"):
            config.add_section("metadata")
        config.set("metadata", "title", title)
        config.set("metadata", "author", author)
        config.set("metadata", "uid", uid)
        with open(os.path.join(title, self.config_filename), "wb") as f:
            config.write(f)

        #
        # Create the basic skeleton underneath the root directory
        #
        print "Create directory structure"
        os.mkdir(os.path.join(title, "OEBPS"))
        os.mkdir(os.path.join(title, "OEBPS/content"))
        os.mkdir(os.path.join(title, SOURCE_DIRPATH))
        print "Copy static files"
        shutil.copy(os.path.join(from_dirpath, "build.cmd"), os.path.join(title, "build.cmd"))

        xml = fill_in_content(
          etree.parse(os.path.join(from_dirpath, "OEBPS/content.opf")),
          title, author, uid
        )
        write_xml(xml, os.path.join(title, "OEBPS/content.opf"))

        xml = fill_in_toc(
          etree.parse(os.path.join(from_dirpath, "OEBPS/toc.ncx")),
          title, author, uid
        )
        write_xml(xml, os.path.join(title, "OEBPS/toc.ncx"))

    def _set_headings(self, title, tree):
        """Replace certain paragraphs by header levels. Typically, this
        will be used to replace "<p>Chapter x</p>" by "<h2>Chapter x</h2>.
        If it is needed to replace only a part of the paragraph then a P
        tag can be used as the replacement.
        """
        document_headings = self.document_headings[title] = []
        if not self.headings:
            return
        index = 1
        for heading, pattern in self.headings:
            print "Looking for", repr(pattern), "to replace by", heading
            paragraphs = list(tree.iter("{*}*"))
            for p in paragraphs:
                text = "".join(p.itertext()).strip()
                if not text: continue
                match = re.search(pattern, text, flags=re.UNICODE|re.IGNORECASE|re.DOTALL)
                if match:
                    if match.groups():
                        match_text = match.group(1).strip()
                    else:
                        match_text = match.group().strip()
                    print "Found", repr(match_text)
                    id = "nav-%d" % index
                    p.tag = heading
                    p.set("id", id)
                    document_headings.append((id, " ".join(match_text.split())))
                    index += 1

        if len(document_headings) < 2:
            print "WARNING: Only %d headings found for %s" % (len(document_headings), title)

    def build(self, dirpath=".", epub_filepath=None):
        """Copy the files from source, transforming them in any
        way necessary on the journey. Generate appropriate content and
        toc files.
        """
        self._set_from_config(os.path.join(dirpath, self.config_filename))
        if not self.title:
          raise RuntimeError("No epub.ini found in %s (or not populated). Are you running in a content directory?" % dirpath)

        #
        # Always add the current directory to the path, not the
        # data directory. This is to allow for a set of individual
        # documents, each with its own set of files, but sharing
        # the same transform.
        #
        source_dirpath = os.path.join(dirpath, SOURCE_DIRPATH)
        data_dirpath = os.path.join(dirpath, "OEBPS")
        dest_dirpath = os.path.join(data_dirpath, "content")
        print "Source:", source_dirpath
        print "Data:", data_dirpath
        print "Dest:", dest_dirpath
        if epub_filepath is None:
            epub_filepath = "%s.epub" % self.title
        if os.path.exists(epub_filepath):
            os.remove(epub_filepath)

        #
        # Clear out any files already in the final content path
        #
        for filepath in glob.glob(os.path.join(dest_dirpath, "*.xhtml")):
            print "Removing %s..." % filepath
            os.remove(filepath)

        #
        # Generate front page
        #
        xml = etree.XML(XHTML_DOCUMENT)
        head = xml.find("{%s}head" % xml.nsmap[None])
        title = etree.Element("title", nsmap=xml.nsmap)
        title.text = self.title
        head.append(title)
        body = xml.find("{%s}body" % xml.nsmap[None])
        etree.SubElement(body, "h1", style="text-align:center;").text = self.title
        etree.SubElement(body, "h2", style="text-align:center;").text = self.author
        with open(os.path.join(dest_dirpath, "front.xhtml"), "wb") as f:
            f.write(
                etree.tostring(
                    xml,
                    pretty_print=True,
                    xml_declaration=True,
                    encoding="utf-8"
                )
            )


        names=["front"]
        #
        # Iterate over the files in source, stripping off
        # any leading numbers which are assumed to be there for
        # the purposes of ordering.
        #
        # Pass the munged name and the bytes of HTML to the transformed
        # function which will manipulate the data and return the
        # transformed bytes.
        #
        # Build up the list of chapters from the files and generate
        # appropriate content.opf and toc.ncx files.
        #
        source_files = glob.glob(os.path.join(source_dirpath, "*.xhtml"))
        print "Source files:", source_files
        for filepath in sorted(source_files):
            filename = os.path.basename(filepath)
            base, ext = os.path.splitext(filename)
            name = base.lstrip(" _0123456789")
            names.append(name)
            print name
            tree = etree.parse(filepath)
            if self.headings:
                self._set_headings(name, tree)
            write_xml(tree, os.path.join(dest_dirpath, "%s.xhtml" % name), doctype=XHTML_DOCTYPE)

        print "Writing content.opf"
        xml = fill_in_content(
          etree.parse(os.path.join(data_dirpath, "content.opf")),
          title=self.title, author=self.author, uid=self.uid,
          names=names, headings=self.document_headings
        )
        write_xml(xml, os.path.join(data_dirpath, "content.opf"))

        print "Writing toc.ncx"
        xml = fill_in_toc(
          etree.parse(os.path.join(data_dirpath, "toc.ncx")),
          title=self.title, author=self.author, uid=self.uid,
          names=names, headings=self.document_headings
        )
        write_xml(xml, os.path.join(data_dirpath, "toc.ncx"))

        print "Building epub"
        with zipfile.ZipFile(epub_filepath, "w") as z:
            z.write(os.path.join(HERE, "template", "mimetype"), "mimetype", zipfile.ZIP_STORED)
            z.write(os.path.join(HERE, "template", "META-INF", "container.xml"), "META-INF/container.xml", zipfile.ZIP_DEFLATED)
            data_dirname = os.path.dirname(data_dirpath)
            for dirpath, dirnames, filenames in os.walk(data_dirpath):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    print "filepath=>", filepath
                    z.write(filepath, filepath[len(data_dirname):], zipfile.ZIP_DEFLATED)

    @staticmethod
    def _collect_metadata(title=None, author=None, uid=None):
        if not title:
            title = raw_input("Title: ").strip()
        if os.path.exists(title):
            raise RuntimeError("There is already a directory named %s" % title)
        if not author:
            author = raw_input("Author: ").strip()
        default_uid = as_code(title) + "-" + time.strftime("%Y%m%d%H%M%S")
        if not uid:
            uid = default_uid
        return title, author, uid

    def clone(self, clone_from=None, title=None, author=None, uid=None, sourcepath=None):
        """Parallel to startup: copy an existing directory structure, including
        the epub.ini, replacing the title, author & uid as necessary.
        """
        if not clone_from:
            clone_from = raw_input("Clone from: ").strip()
        if not os.path.exists(clone_from):
            raise RuntimeError("There is no directory named %s" % clone_from)
        title, author, uid = self._collect_metadata(title, author, uid)
        self._generate_skeleton(title, author, uid, from_dirpath=clone_from)

        #
        # Offer the possibility of copying from a path
        # (typically a URL) and generating the XHTML source
        # file with the same name as the document.
        #
        if sourcepath is None:
            sourcepath = raw_input("Source: ")
        if sourcepath is not None:
            dirpath = os.path.join(title, "source")
            convert_to_xhtml(sourcepath, dirpath)
            for filepath in glob.glob(os.path.join(dirpath, "*.xhtml")):
                os.rename(filepath, os.path.join(dirpath, "%s.xhtml" % title))
                break

    def startup(self, title=None, author=None, uid=None):
        """Collect basic metadata and generate an empty directory
        structure including an .ini file and static support files.

        The dynamically-generated support files, eg content.opf, will
        be populated by the build command once .xhtml files have been
        placed in source/
        """
        title, author, uid = self._collect_metadata(title, author, uid)
        self._generate_skeleton(title, author, uid, from_dirpath=TEMPLATE_DIRPATH)

    def xhtml(self, path, outpath=None):
        """Take a URL or a file or a directory of files and convert them
        to valid XHTML, stripping out unnecessary tags or attributes in the
        process. The resulting file or files are given an .xhtml extension and placed
        in a directory (a temporary one is generated if necessary) which is then opened
        so the files can be examined or copied.
        """
        if outpath is None:
            dirpath = tempfile.mkdtemp()
        else:
            dirpath = outpath
        if path.startswith("http://"):
            filepaths = [path]
        elif os.path.isfile(path):
            filepaths = [path]
        else:
            filepaths = sorted(
                glob.glob(os.path.join(path, "*.html")) +
                glob.glob(os.path.join(path, "*.xhtml")) +
                glob.glob(os.path.join(path, "*.htm"))
            )

        for filepath in filepaths:
            print filepath
            convert_to_xhtml(filepath, dirpath)

        os.startfile(dirpath)

    def main(self, command, *args, **kwargs):
        function = getattr(self, command.lower(), None)
        if function:
            function(*args, **kwargs)
        else:
            raise RuntimeError("Unknown command: %s" % command)

if __name__ == '__main__':
    EPub().main(*sys.argv[1:])
