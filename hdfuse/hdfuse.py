#!/usr/bin/env python3

# Copyright (c) 2011 Tobias Richter <Tobias.Richter@diamond.ac.uk>
#
# Permission to use, copy, modify, and distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

from __future__ import with_statement

import argparse
import logging
import os
import subprocess
import tempfile
from errno import EACCES
from multiprocessing import Process
from threading import Lock

import h5py

from fuse import FUSE, FuseOSError, Operations


class HDFuse5(Operations):
    def __init__(self, root):
        self.root = os.path.realpath(root)
        self.rwlock = Lock()

    def __call__(self, op, path, *args):
        return super(HDFuse5, self).__call__(op, self.root + path, *args)

    class PotentialHDFFile:
        def __init__(self, path):
            # self.log(f"Opening {path}")
            self.dsattrs = {
                "user.ndim": (lambda x: x.value.ndim),
                "user.shape": (lambda x: x.value.shape),
                "user.dtype": (lambda x: x.value.dtype),
                "user.size": (lambda x: x.value.size),
                "user.itemsize": (lambda x: x.value.itemsize),
                "user.dtype.itemsize": (lambda x: x.value.dtype.itemsize),
            }
            self.fullpath = path
            self.nexusfile = None
            self.nexushandle = None
            self.internalpath = "/"
            if os.path.lexists(path):
                self.testHDF(path)
            else:
                components = path.split("/")
                # remote parts of the path until the nxs file is found
                # we end up with internalpath having the bits that were removed
                # as they are used internally in the nxs file to traverse it
                for i in range(len(components), 0, -1):
                    test = "/".join(components[:i])
                    if self.testHDF(test):
                        self.internalpath = "/".join(components[i - len(components):])
                        break

        def testHDF(self, path):
            if os.path.isfile(path):
                try:
                    self.nexushandle = h5py.File(path, 'r')
                    self.nexusfile = path
                    return True
                except Exception:
                    pass
                return False

        def __del__(self):
            if self.nexushandle is not None:
                try:
                    self.nexushandle.close()
                except Exception:
                    pass

        def makeIntoDir(self, statdict):
            statdict["st_mode"] = statdict["st_mode"] ^ 0o100000 | 0o040000
            for i in [[0o400, 0o100], [0o40, 0o10], [0o4, 0o1]]:
                if (statdict["st_mode"] & i[0]) != 0:
                    statdict["st_mode"] = statdict["st_mode"] | i[1]
            return statdict

        def getattr(self):
            if self.nexusfile is not None:
                st = os.lstat(self.nexusfile)
            else:
                st = os.lstat(self.fullpath)
            statdict = dict(
                (key, getattr(st, key))
                for key in ('st_atime', 'st_ctime', 'st_gid', 'st_mode', 'st_mtime', 'st_nlink', 'st_size', 'st_uid'))
            if self.nexusfile is not None:
                if self.internalpath == "/":
                    statdict = self.makeIntoDir(statdict)
                elif isinstance(self.nexushandle[self.internalpath], h5py.Group):
                    statdict = self.makeIntoDir(statdict)
                    statdict["st_size"] = 0
                elif isinstance(self.nexushandle[self.internalpath], h5py.Dataset):
                    ob = self.nexushandle[self.internalpath].value
                    statdict["st_size"] = ob.size * ob.itemsize
                    statdict["st_mode"] = 0o100400  # mark datasets as read only
            return statdict

        def getxattr(self, name):
            if self.nexushandle is None:
                return b""
            rawname = name[5:]
            if rawname in self.nexushandle[self.internalpath].attrs.keys():
                attrval = str(self.nexushandle[self.internalpath].attrs[rawname])
                return attrval
            if isinstance(self.nexushandle[self.internalpath], h5py.Dataset):
                if name in self.dsattrs.keys():
                    attrval = str(self.dsattrs[name](self.nexushandle[self.internalpath]))
                    return attrval
            return b""

        def listxattr(self):
            if self.nexushandle is None:
                return []
            xattrs = []
            for i in self.nexushandle[self.internalpath].attrs.keys():
                xattrs.append("user." + i)
            if isinstance(self.nexushandle[self.internalpath], h5py.Dataset):
                for i in self.dsattrs.keys():
                    xattrs.append(i)
            return xattrs

        def listdir(self):
            if self.nexushandle is None:
                items = ['.', '..'] + [name for name in os.listdir(self.fullpath)]
            else:
                items = self.nexushandle[self.internalpath].items()
                items = ['.', '..'] + [item[0] for item in items]
            return items

        def access(self, mode):
            path = self.fullpath
            if self.nexusfile is not None:
                path = self.nexusfile
                if mode == os.X_OK:
                    mode = os.R_OK
            if not os.access(path, mode):
                raise FuseOSError(EACCES)

        def read(self, size, offset, fh, lock):
            if self.nexushandle is None or self.internalpath == "/":
                with lock:
                    os.lseek(fh, offset, 0)
                return os.read(fh, size)
            if isinstance(self.nexushandle[self.internalpath], h5py.Dataset):
                return self.nexushandle[self.internalpath].value.tostring()[offset:offset + size]

        def open(self, flags):
            if self.nexushandle is None or self.internalpath == "/":
                return os.open(self.fullpath, flags)
            return 0

        def close(self, fh):
            if self.nexushandle is None or self.internalpath == "/":
                return os.close(fh)
            return 0

    def access(self, path, mode):
        self.PotentialHDFFile(path).access(mode)

    def read(self, path, size, offset, fh):
        return self.PotentialHDFFile(path).read(size, offset, fh, self.rwlock)

    def getattr(self, path, fh=None):
        return self.PotentialHDFFile(path).getattr()

    def getxattr(self, path, name):
        return self.PotentialHDFFile(path).getxattr(name)

    def listxattr(self, path):
        return self.PotentialHDFFile(path).listxattr()

    def readdir(self, path, fh):
        return self.PotentialHDFFile(path).listdir()

    def release(self, path, fh):
        return self.PotentialHDFFile(path).close(fh)

    def statfs(self, path):
        stv = os.statvfs(path)
        return dict((key, getattr(stv, key)) for key in ('f_bavail', 'f_bfree', 'f_blocks', 'f_bsize', 'f_favail',
                                                         'f_ffree', 'f_files', 'f_flag', 'f_frsize', 'f_namemax'))

    def open(self, path, flags):
        return self.PotentialHDFFile(path).open(flags)

    truncate = None
    write = None
    rename = None
    symlink = None
    setxattr = None
    removexattr = None
    link = None
    mkdir = None
    mknod = None
    rmdir = None
    unlink = None
    chmod = None
    chown = None
    create = None
    fsync = None
    flush = None
    utimens = os.utime
    readlink = os.readlink


def main():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-d', '--directory', help="Directory in which the HDF files are located.")
    parser.add_argument('-e',
                        '--editor',
                        default='vim',
                        help="Editor in which the directory will be opened for navigation.")
    # parser.add_argument('--keep-mount',
    #                     action='store_true',
    #                     help="If True the mount will not be removed after the editor has exited.")
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Log verbosity level. Available options are: TRACE, DEBUG, INFO, WARN, CRITICAL",
    )

    args = parser.parse_args()

    logging.basicConfig(level=args.log_level, format='%(asctime)s :: %(message)s')

    with tempfile.TemporaryDirectory() as temp_mount_dir:
        logging.info(f"Running in tempdir: {temp_mount_dir}")
        hdfuse = HDFuse5(args.directory)
        p = Process(target=FUSE, args=(hdfuse, temp_mount_dir))
        p.start()

        logging.debug(f"Entering {args.editor}")
        err = subprocess.check_call([args.editor, temp_mount_dir])
        if err != 0:
            logging.error(f"{args.editor} exited with error code: {err}")

        logging.debug("Joining process")
        p.join()

        logging.debug("Stopping mount")
        err = subprocess.check_call(['fusermount', '-u', temp_mount_dir])
        if err != 0:
            logging.critical(f"Failed to unmount directory {temp_mount_dir} with fusermount. Error code: {err}")
        else:
            logging.info(f"Directory successfully unmounted")


if __name__ == "__main__":
    main()
