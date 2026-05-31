#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
find_duplicates.py - Duplicate File Finder for QNAP NAS

Finds duplicate files by comparing file sizes first, then MD5 hashes.
Designed for Python 2.7 on QNAP NAS systems (no external dependencies).

USAGE EXAMPLES:
    # Basic scan of a directory
    python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia

    # Scan multiple directories
    python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia /share/CACHEDEV1_DATA/Download

    # Save results to a file
    python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia --output duplicates_report.txt

    # Change minimum file size (default 1MB)
    python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia --min-size 10485760

    # Hashing uses 1 thread by default (best for spinning-disk RAID); bump it
    # only on SSD/NVMe arrays where parallel sequential reads actually help
    python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia --workers 8

    # Speed up the directory scan itself (parallel stat walk, default 8 threads)
    python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia --scan-workers 16

    # Include QNAP metadata directories (normally skipped)
    python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia --include-metadata

    # Follow symbolic links
    python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia --follow-symlinks

    # Show progress during scan
    python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia --verbose

    # Disable progress indicator (useful for logging to file)
    python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia --no-progress

    # Exit 0 even when duplicates are found (default exits 1 on duplicates)
    python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia --zero-exit

AUTHOR: Generated for QNAP NAS duplicate file management
PYTHON: 2.7 (standard library only)
"""

from __future__ import print_function
import os
import sys
import argparse
import hashlib
import json
import stat
import time
import threading
from collections import defaultdict

# Queue moved/renamed between Python 2 and 3. Used to feed the hashing thread
# pool below.
try:
    from Queue import Queue, Empty  # Python 2
except ImportError:
    from queue import Queue, Empty  # Python 3

# raw_input was renamed to input in Python 3. Keep the 2.7 name working while
# also letting the script run unmodified under Python 3 (used for testing the
# deletion logic off-NAS).
try:
    raw_input
except NameError:
    raw_input = input

# dict.iteritems() (a lazy iterator) exists only on Python 2; on Python 3 the
# plain .items() view is already lazy. This helper picks the right one so large
# dicts (e.g. the per-size tally) are iterated without materialising a list on
# Py2, while keeping the off-NAS Py3 test path working.
try:
    dict.iteritems

    def _iteritems(d):
        return d.iteritems()
except AttributeError:
    def _iteritems(d):
        return d.items()

# ============================================================================
# Constants
# ============================================================================

DEFAULT_MIN_SIZE = 1024 * 1024  # 1 MB in bytes

# Directories to skip by default on QNAP
SKIP_DIRS = frozenset([
    'lost+found',
    '.@__thumb',
])

# ============================================================================
# Utility Functions
# ============================================================================

def human_readable_size(size_bytes):
    """Convert bytes to human readable string (B/KB/MB/GB/TB)."""
    if size_bytes < 0:
        return "0 B"

    for unit in ['B', 'KB', 'MB', 'GB', 'TB', 'PB']:
        if abs(size_bytes) < 1024.0:
            if unit == 'B':
                return "{0} {1}".format(int(size_bytes), unit)
            return "{0:.2f} {1}".format(size_bytes, unit)
        size_bytes /= 1024.0

    return "{0:.2f} PB".format(size_bytes)


def human_readable_duration(seconds):
    """Format a duration as e.g. '4.20s', '2m 05s', or '1h 02m 03s'."""
    if seconds < 60:
        return "{0:.2f}s".format(seconds)
    total = int(seconds)
    if total < 3600:
        return "{0}m {1:02d}s".format(total // 60, total % 60)
    return "{0}h {1:02d}m {2:02d}s".format(
        total // 3600, (total % 3600) // 60, total % 60)


def should_skip_dir(dirname, include_metadata=False):
    """Check if a directory should be skipped."""
    # Skip directories starting with '@' (QNAP system/metadata)
    if not include_metadata and dirname.startswith('@'):
        return True

    # Skip known system directories
    if dirname in SKIP_DIRS:
        return True

    return False


def compute_md5(filepath, chunk_size=1048576, progress_callback=None):
    """
    Compute MD5 hash of a file.

    Uses chunked reading for memory efficiency on large files.
    Returns None if file cannot be read.

    Args:
        filepath: Path to file
        chunk_size: Read chunk size (default 1MB for faster I/O on large files)
        progress_callback: Optional function(bytes_read, total_bytes) called periodically
    """
    md5 = hashlib.md5()
    try:
        file_size = os.path.getsize(filepath)
        bytes_read = 0

        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                md5.update(chunk)
                bytes_read += len(chunk)

                # Call progress callback if provided
                if progress_callback:
                    progress_callback(bytes_read, file_size)

        return md5.hexdigest()
    except (IOError, OSError):
        return None


def compute_partial_md5(filepath, sample_size=65536):
    """
    Compute MD5 of the first and last N bytes of a file for quick pre-filtering.

    Sampling both head and tail catches files that share an identical container
    header (common with media containers) but differ later in the stream, for
    near-zero extra I/O cost. Returns None if file cannot be read.
    """
    md5 = hashlib.md5()
    try:
        with open(filepath, 'rb') as f:
            # Head sample
            md5.update(f.read(sample_size))

            # Tail sample, only if the file is large enough that the tail does
            # not overlap the head we already read (avoids hashing nothing new
            # and avoids a pointless seek on small files).
            try:
                file_size = os.fstat(f.fileno()).st_size
            except OSError:
                file_size = 0

            if file_size > sample_size * 2:
                f.seek(-sample_size, os.SEEK_END)
                md5.update(f.read(sample_size))

        return md5.hexdigest()
    except (IOError, OSError):
        return None


# ============================================================================
# Progress Display
# ============================================================================

class ProgressDisplay(object):
    """Handles in-place progress updates on the terminal."""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.last_update = 0
        self.update_interval = 0.1  # Update every 100ms max
        self.last_line_length = 0

    def update(self, message, force=False):
        """Update the progress line in-place."""
        if not self.enabled:
            return

        # Throttle updates to avoid excessive I/O
        now = time.time()
        if not force and (now - self.last_update) < self.update_interval:
            return
        self.last_update = now

        # Truncate message if too long for typical terminal
        max_width = 100
        if len(message) > max_width:
            message = message[:max_width - 3] + '...'

        # Pad with spaces to overwrite previous line content
        padded = message.ljust(self.last_line_length)
        self.last_line_length = len(message)

        sys.stdout.write('\r' + padded)
        sys.stdout.flush()

    def clear(self):
        """Clear the progress line."""
        if not self.enabled:
            return
        sys.stdout.write('\r' + ' ' * self.last_line_length + '\r')
        sys.stdout.flush()
        self.last_line_length = 0


# ============================================================================
# Scanner Class
# ============================================================================

class DuplicateFinder(object):
    """Finds duplicate files using size-first, then hash approach."""

    def __init__(self, min_size=DEFAULT_MIN_SIZE, include_metadata=False,
                 follow_symlinks=False, verbose=False, show_progress=True,
                 workers=1, scan_workers=8):
        # Floor at 1 byte: zero-byte files reclaim nothing if deleted and are
        # often structural touch-files, so never group/delete them even if the
        # caller passes --min-size 0.
        self.min_size = max(1, min_size)
        self.include_metadata = include_metadata
        self.follow_symlinks = follow_symlinks
        self.verbose = verbose
        self.workers = max(1, workers)            # hashing threads
        self.scan_workers = max(1, scan_workers)  # directory-walk threads
        self.progress = ProgressDisplay(enabled=show_progress)

        # Statistics
        self.total_files_scanned = 0
        self.total_bytes_scanned = 0
        self.files_skipped_size = 0
        self.files_skipped_error = 0
        self.files_skipped_inode = 0
        self.dirs_scanned = 0
        self.dirs_skipped = 0
        self._candidates_collected = 0

        # Wall-clock time of each phase (set by scan_directories/find_duplicates).
        self.scan_pass1_seconds = 0.0
        self.scan_pass2_seconds = 0.0
        self.scan_total_seconds = 0.0
        self.prefilter_seconds = 0.0
        self.hash_seconds = 0.0

        # Phase 1 runs in two passes (see scan_directories). The first pass
        # only tallies a {size: count} table; the second keeps paths for the
        # size-colliding files alone. size_groups therefore holds just the
        # duplicate candidates, not every file scanned -- so peak memory
        # scales with the number of collisions, not the total file count.
        # {size_in_bytes: [filepath1, filepath2, ...]}
        self.size_groups = defaultdict(list)
        self.unique_sizes = 0  # distinct file sizes seen (for the summary)

        # (st_dev, st_ino) pairs already recorded, so hardlinks to the same
        # inode aren't reported as reclaimable duplicates (deleting one frees
        # nothing). Reset between passes. Pass 1 only adds true hardlinks
        # (nlink > 1) to keep this bounded by hardlink count on huge trees;
        # pass 2 adds every inode (it's the pass that decides what's collected),
        # which also makes the final result idempotent across overlapping input
        # paths (e.g. /a and /a/b given together).
        self._seen_inodes = set()

    def log(self, message):
        """Print message if verbose mode is enabled."""
        if self.verbose:
            self.progress.clear()
            print(message, file=sys.stderr)

    @staticmethod
    def _shorten_dir(path, width=40):
        """Trim a directory path to its tail for a fixed-width display."""
        if len(path) > width:
            return '...' + path[-(width - 3):]
        return path

    def _update_scan_progress(self, current_dir):
        """Update the pass-1 (size tally) progress display."""
        msg = "[Scan 1/2] Files: {0} | Size: {1} | Dirs: {2} | {3}".format(
            self.total_files_scanned,
            human_readable_size(self.total_bytes_scanned),
            self.dirs_scanned,
            self._shorten_dir(current_dir)
        )
        self.progress.update(msg)

    def _update_collect_progress(self, current_dir, collected):
        """Update the pass-2 (path collection) progress display."""
        msg = "[Scan 2/2] Candidates: {0} | {1}".format(
            collected,
            self._shorten_dir(current_dir)
        )
        self.progress.update(msg)

    def _valid_paths(self, paths):
        """Filter to existing directories, warning about the rest (once)."""
        valid = []
        for base_path in paths:
            if not os.path.exists(base_path):
                self.progress.clear()
                print("Warning: Path does not exist: {0}".format(base_path),
                      file=sys.stderr)
                continue
            if not os.path.isdir(base_path):
                self.progress.clear()
                print("Warning: Not a directory: {0}".format(base_path),
                      file=sys.stderr)
                continue
            valid.append(base_path)
        return valid

    def scan_directories(self, paths):
        """
        Scan one or more directory paths for files in two passes.

        Pass 1 tallies how many files share each exact byte size, keeping only
        a {size: count} table -- no path strings. Pass 2 re-walks the same
        paths and retains paths solely for the sizes that occurred more than
        once -- the only files that could possibly be duplicates.

        Splitting the walk this way keeps peak memory proportional to the
        number of size-colliding files instead of the total file count, which
        matters on a NAS with limited RAM and very large trees. The cost is a
        second metadata (stat) walk, which is cheap next to the hashing phase
        that follows.

        Both passes use _parallel_walk: the tree is walked by self.scan_workers
        threads, which overlaps the per-entry stat/listdir seek latency that
        otherwise leaves a NAS array idle. Per-directory results are merged into
        the shared tallies single-threaded under one lock. The result is
        deterministic and independent of worker count / thread scheduling: a
        file reachable by several paths (hardlinks, or symlinks under
        --follow-symlinks) is represented by its lexicographically smallest
        path (see pass 2 below).

        Pass 1 only de-dups true hardlinks (nlink > 1) to bound its inode set by
        hardlink count, so its scan stats may double-count a non-hardlinked file
        reached via overlapping input paths. Pass 2 de-dups on every inode, so
        the collected candidate set -- and thus the final result -- is correct
        regardless.
        """
        valid_paths = self._valid_paths(paths)

        t0 = time.time()

        # --- Pass 1: count files per size (parallel walk) -----------------
        self.log("Scanning (pass 1/2): {0}".format(", ".join(valid_paths)))
        size_counts = defaultdict(int)

        def pass1_merge(dirpath, file_entries, dirs_skipped, errors):
            # Runs under the walk lock: the only place pass-1 stats mutate.
            self.dirs_scanned += 1
            self.dirs_skipped += dirs_skipped
            self.files_skipped_error += errors
            for filepath, file_size, inode_key, nlink in file_entries:
                if file_size < self.min_size:
                    self.files_skipped_size += 1
                    continue
                # Inode de-dup, only for true hardlinks (nlink > 1). Decided
                # here (under the lock) so concurrent workers can't both record
                # the same inode. nlink == 1 files are NOT tracked -- that keeps
                # this set proportional to hardlink count rather than total file
                # count (a big RAM win on multi-million-file trees). The cost is
                # purely in the stats: a nlink == 1 file reachable via overlapping
                # input paths may be tallied twice here, inflating its size count.
                # That only ever turns a unique size into a spurious candidate;
                # pass 2 keeps full inode dedup, so the final result is unchanged.
                if inode_key is not None and nlink > 1:
                    if inode_key in self._seen_inodes:
                        self.files_skipped_inode += 1
                        continue
                    self._seen_inodes.add(inode_key)
                self.total_files_scanned += 1
                self.total_bytes_scanned += file_size
                size_counts[file_size] += 1
            self._update_scan_progress(dirpath)

        self._parallel_walk(valid_paths, pass1_merge, self.scan_workers)
        self.progress.clear()
        t_pass1 = time.time()
        self.scan_pass1_seconds = t_pass1 - t0

        self.unique_sizes = len(size_counts)
        candidate_sizes = set(size for size, n in _iteritems(size_counts) if n > 1)

        # Free the pass-1 working set before pass 2 builds its own.
        size_counts = None
        self._seen_inodes = set()

        if not candidate_sizes:
            self.scan_pass2_seconds = 0.0
            self.scan_total_seconds = time.time() - t0
            return

        # --- Pass 2: collect paths for colliding sizes only (parallel) ----
        self.log("Scanning (pass 2/2): {0}".format(", ".join(valid_paths)))
        self._candidates_collected = 0
        # inode_key -> index of its path within size_groups[size]. Lets us keep
        # the lexicographically smallest path when one inode is reachable by
        # several paths, so the collected set is deterministic regardless of
        # which worker thread reached it first.
        inode_index = {}

        def pass2_merge(dirpath, file_entries, dirs_skipped, errors):
            # Runs under the walk lock. Deliberately does NOT touch the pass-1
            # counters (dirs_scanned, files_skipped_*, total_*) -- those were
            # finalised in pass 1; re-counting here would double them.
            # Full inode dedup here (every inode_key, not just nlink > 1): this
            # is the pass that decides what gets collected/deleted, so it must
            # collapse hardlinks AND files reached via overlapping input paths.
            # The set stays small -- it's bounded by candidate-size files only.
            for filepath, file_size, inode_key, _nlink in file_entries:
                if file_size not in candidate_sizes:
                    continue
                if inode_key is None:
                    # No usable inode number -> can't dedup; keep every path.
                    self.size_groups[file_size].append(filepath)
                    self._candidates_collected += 1
                    continue
                if inode_key in inode_index:
                    # Same inode via another path (hardlink, or a symlink under
                    # --follow-symlinks): keep the smallest path deterministically.
                    idx = inode_index[inode_key]
                    if filepath < self.size_groups[file_size][idx]:
                        self.size_groups[file_size][idx] = filepath
                    continue
                self.size_groups[file_size].append(filepath)
                inode_index[inode_key] = len(self.size_groups[file_size]) - 1
                self._candidates_collected += 1
            self._update_collect_progress(dirpath, self._candidates_collected)

        self._parallel_walk(valid_paths, pass2_merge, self.scan_workers)
        self.progress.clear()
        t_end = time.time()
        self.scan_pass2_seconds = t_end - t_pass1
        self.scan_total_seconds = t_end - t0

    @staticmethod
    def _await_threads(threads):
        """
        Join worker threads while staying responsive to Ctrl+C.

        A bare Thread.join()/Queue.join() with no timeout is UNINTERRUPTIBLE on
        Python 2: the main thread parks in a C lock acquire and CPython only
        raises KeyboardInterrupt in the main thread between bytecodes, so the
        signal can't fire until the join returns. Polling with a short timeout
        keeps returning to the bytecode loop (the underlying time.sleep takes
        the signal), so Ctrl+C is delivered within ~one tick. Workers are daemon
        threads, so a propagating KeyboardInterrupt tears them down at exit.
        """
        while True:
            alive = [t for t in threads if t.is_alive()]
            if not alive:
                return
            for t in alive:
                t.join(0.2)

    def _parallel_walk(self, base_paths, merge_batch, worker_count):
        """
        Walk base_paths with a pool of worker_count threads.

        Each worker pops a directory off a shared queue, lists it and stats its
        entries WITHOUT holding any lock (this is the seek-bound part we want to
        overlap), then calls merge_batch(dirpath, file_entries, dirs_skipped,
        errors) once for that directory while holding a single shared lock.
        file_entries is a list of (filepath, size, inode_key, nlink) where
        inode_key is (st_dev, st_ino) or None when the fs reports no usable
        inode, and nlink is the target's link count (pass 1 uses it to track
        only true hardlinks; pass 2 dedups on inode_key regardless). Only
        regular files are listed -- fifos/sockets/device nodes are skipped.

        Subdirectories discovered are pushed back onto the queue, so the work
        set grows as the walk descends. Termination therefore relies on
        Queue.join()/task_done() (NOT get_nowait()/Empty, which would race with
        a worker that is about to enqueue children): join() returns only once
        every enqueued directory -- including ones discovered mid-walk -- has
        been processed, after which sentinels wake the blocked workers.

        Symlink/skip semantics match the previous os.walk: with follow_symlinks
        off, symlinks are skipped entirely; with it on, a symlinked dir is
        descended and a symlinked file is stat'd through to its target (so size
        and inode come from the target). No symlink-loop detection -- same as
        os.walk(followlinks=True).

        Memory: the queue holds pending directory PATH strings (frontier
        breadth), not files, so it does not change the two-pass memory bound.
        """
        work = Queue()
        for base in base_paths:
            work.put(base)

        lock = threading.Lock()
        sentinel = object()

        def process_dir(dirpath):
            try:
                names = os.listdir(dirpath)
            except (OSError, IOError):
                # Unlistable dir: like os.walk's default onerror, it yields
                # nothing and is not counted as scanned.
                return

            subdirs = []
            file_entries = []
            dirs_skipped = 0
            errors = 0

            for name in names:
                full = os.path.join(dirpath, name)
                try:
                    st = os.lstat(full)
                except (OSError, IOError):
                    errors += 1
                    continue
                mode = st.st_mode

                if stat.S_ISLNK(mode):
                    if not self.follow_symlinks:
                        continue  # skip symlinks entirely
                    # Following: resolve to the target for classification,
                    # size and inode (matches the old os.stat behaviour).
                    try:
                        st = os.stat(full)
                    except (OSError, IOError):
                        errors += 1
                        continue
                    mode = st.st_mode
                    if stat.S_ISDIR(mode):
                        if should_skip_dir(name, self.include_metadata):
                            dirs_skipped += 1
                        else:
                            subdirs.append(full)
                        continue
                    # else: a symlink to a file -> fall through to file handling
                elif stat.S_ISDIR(mode):
                    if should_skip_dir(name, self.include_metadata):
                        dirs_skipped += 1
                    else:
                        subdirs.append(full)
                    continue

                # Only regular files (or a followed symlink whose target is a
                # regular file) are hashable. Skip fifos/sockets/device nodes:
                # the old os.walk listed them as files, but compute_md5 would
                # block forever on a fifo and read unbounded data from a device.
                if not stat.S_ISREG(mode):
                    continue
                inode_key = None
                if st.st_ino != 0:
                    inode_key = (st.st_dev, st.st_ino)
                # nlink lets pass 1 keep only true hardlinks (nlink > 1) in its
                # inode set; pass 2 dedups on inode_key regardless (see merges).
                file_entries.append((full, st.st_size, inode_key, st.st_nlink))

            with lock:
                merge_batch(dirpath, file_entries, dirs_skipped, errors)

            # Queue is thread-safe; enqueue children AFTER the merge but BEFORE
            # this dir's task_done (in the worker) so join() can't finish early.
            for child in subdirs:
                work.put(child)

        def worker():
            while True:
                item = work.get()
                if item is sentinel:
                    work.task_done()
                    return
                try:
                    process_dir(item)
                except (OSError, IOError):
                    pass  # one bad directory must not kill the worker
                finally:
                    work.task_done()

        n = max(1, worker_count)  # do NOT cap by len(base_paths); work fans out
        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.daemon = True
            t.start()

        # Wait for every dir (including ones discovered mid-walk) to finish.
        # Queue.join() has no timeout and is uninterruptible on Python 2, so run
        # it in a daemon thread and poll an Event with a timeout instead -- that
        # keeps Ctrl+C responsive (see _await_threads).
        finished = threading.Event()
        waiter = threading.Thread(target=lambda: (work.join(), finished.set()))
        waiter.daemon = True
        waiter.start()
        while not finished.wait(0.2):
            pass

        for _ in range(n):
            work.put(sentinel)           # wake the blocked workers so they exit
        self._await_threads(threads)

    def _parallel_hash(self, filepaths, hash_fn, label, worker_count=None,
                       show_byte_progress=False):
        """
        Hash every path in `filepaths` with a pool of `worker_count` threads
        (default self.workers).

        hash_fn(filepath) -> hex digest, or None if the file can't be read.
        Returns a {filepath: digest} dict; unreadable files are omitted and
        counted into files_skipped_error.

        The two callers use different pool sizes on purpose: the pre-filter does
        many small random head+tail reads and wants a deep queue (self.scan_
        workers), while the full hash does large sequential reads that thrash a
        spinning array if too many run at once (self.workers / -j, kept low).

        The work is I/O-bound and both file reads and hashlib.update() (on our
        1 MB / 64 KB buffers) release the GIL, so threads genuinely overlap the
        per-file seek latency that otherwise leaves a multi-disk array idle.
        Grouping the results stays single-threaded in the caller, so nothing
        that decides what gets deleted runs concurrently.
        """
        results = {}
        total = len(filepaths)
        if total == 0:
            return results

        work = Queue()
        for fp in filepaths:
            work.put(fp)

        lock = threading.Lock()
        state = {'done': 0, 'errors': 0}

        def worker():
            while True:
                try:
                    fp = work.get_nowait()
                except Empty:
                    return
                name = os.path.basename(fp)
                if len(name) > 30:
                    name = name[:27] + '...'
                # With show_byte_progress, feed a per-chunk callback into hash_fn
                # so a single multi-GB hash ticks a percentage instead of looking
                # stalled between completed files. The callback takes the same
                # lock that guards the progress line; ProgressDisplay's 0.1s
                # throttle keeps the per-chunk cost negligible.
                if show_byte_progress:
                    def cb(read, size, _name=name):
                        pct = (read * 100 // size) if size else 100
                        with lock:
                            self.progress.update("{0} {1}/{2} | {3} ({4}%)".format(
                                label, state['done'] + 1, total, _name, pct))
                    digest = hash_fn(fp, progress_callback=cb)
                else:
                    digest = hash_fn(fp)
                # Guard shared state AND the progress line (ProgressDisplay is
                # not thread-safe); the write is throttled so this is cheap.
                with lock:
                    state['done'] += 1
                    if digest is None:
                        state['errors'] += 1
                    else:
                        results[fp] = digest
                    self.progress.update("{0} {1}/{2} | {3}".format(
                        label, state['done'], total, name))

        wc = self.workers if worker_count is None else worker_count
        n = max(1, min(wc, total))
        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.daemon = True
            t.start()
        self._await_threads(threads)  # interruptible join (responsive to Ctrl+C)

        self.files_skipped_error += state['errors']
        return results

    def find_duplicates(self):
        """
        Phase 2: Find duplicates by computing hashes for size-matched files.

        Returns a list of duplicate groups:
        [
            {
                'hash': 'abc123...',
                'size': 12345678,
                'files': ['/path/to/file1', '/path/to/file2', ...]
            },
            ...
        ]
        """
        duplicates = []

        # Only process size groups with more than one file
        size_groups_to_check = [
            (size, files) for size, files in _iteritems(self.size_groups)
            if len(files) > 1
        ]

        self.log("Found {0} size groups with potential duplicates".format(
            len(size_groups_to_check)))

        # Upper-bound estimate before pre-filtering (for the summary block).
        upper_bound_files = sum(len(files) for _, files in size_groups_to_check)
        upper_bound_bytes = sum(size * len(files)
                                for size, files in size_groups_to_check)

        # Print summary before hashing
        self.progress.clear()
        print("")
        print("-" * 50)
        print("SCAN COMPLETE - CHECKSUM PHASE STARTING")
        print("-" * 50)
        print("  Unique file sizes found:    {0}".format(self.unique_sizes))
        print("  Size groups with 2+ files:  {0}".format(len(size_groups_to_check)))
        print("  Candidate files (max):      {0}".format(upper_bound_files))
        print("  Data to read (max):         {0}".format(
            human_readable_size(upper_bound_bytes)))
        print("  Scan time (walk):           {0}  (pass 1: {1}, pass 2: {2})".format(
            human_readable_duration(self.scan_total_seconds),
            human_readable_duration(self.scan_pass1_seconds),
            human_readable_duration(self.scan_pass2_seconds)))
        print("-" * 50)
        print("")

        if upper_bound_files == 0:
            print("No potential duplicates found - nothing to hash.")
            return duplicates

        # ------------------------------------------------------------------
        # Phase 2a: pre-filter with a cheap head+tail partial hash.
        #
        # Hash every file in a >2-member group in parallel, then regroup by
        # partial digest (single-threaded). Groups of exactly 2 skip the
        # pre-filter -- it can never eliminate anything -- and go straight to
        # the full hash.
        # ------------------------------------------------------------------
        prefilter_targets = []
        for size, filepaths in size_groups_to_check:
            if len(filepaths) > 2:
                prefilter_targets.extend(filepaths)

        # Pre-filter reads are small and random (head+tail of scattered files),
        # so drive them with the metadata pool (scan_workers) for queue depth --
        # NOT the low -j meant for big sequential reads.
        t_pf0 = time.time()
        partial_hashes = self._parallel_hash(
            prefilter_targets, compute_partial_md5, "[Pre-filter]",
            worker_count=self.scan_workers)

        candidate_groups = []  # list of (size, [filepaths sharing a partial hash])
        for size, filepaths in size_groups_to_check:
            if len(filepaths) > 2:
                partial_groups = defaultdict(list)
                for fp in filepaths:
                    digest = partial_hashes.get(fp)
                    if digest is not None:
                        partial_groups[digest].append(fp)
                for fps in partial_groups.values():
                    if len(fps) > 1:
                        candidate_groups.append((size, fps))
            else:
                candidate_groups.append((size, filepaths))
        self.prefilter_seconds = time.time() - t_pf0

        files_to_hash = sum(len(fps) for _, fps in candidate_groups)
        total_bytes_to_hash = sum(size * len(fps)
                                  for size, fps in candidate_groups)

        # Pre-filter findings + timing (mirrors the SCAN COMPLETE block).
        self.progress.clear()
        print("")
        print("-" * 50)
        print("PRE-FILTER COMPLETE - head+tail partial hash")
        print("-" * 50)
        print("  Files checked (head+tail):  {0}".format(len(prefilter_targets)))
        print("  Eliminated by pre-filter:   {0}".format(
            upper_bound_files - files_to_hash))
        print("  Survivors to full hash:     {0} in {1} groups".format(
            files_to_hash, len(candidate_groups)))
        print("  Data to full-hash:          {0}".format(
            human_readable_size(total_bytes_to_hash)))
        print("  Pre-filter time:            {0}  ({1} threads)".format(
            human_readable_duration(self.prefilter_seconds), self.scan_workers))
        print("-" * 50)
        print("")

        if files_to_hash == 0:
            print("No duplicates survived pre-filtering.")
            return duplicates

        # ------------------------------------------------------------------
        # Phase 2b: full MD5 hash of the survivors, in parallel, then group
        # by digest (single-threaded) so only the I/O fans out. These are large
        # sequential reads -- self.workers (-j) stays low so concurrent streams
        # don't make a spinning array seek-thrash.
        # ------------------------------------------------------------------
        full_targets = []
        for size, filepaths in candidate_groups:
            full_targets.extend(filepaths)

        t_h0 = time.time()
        full_hashes = self._parallel_hash(full_targets, compute_md5, "[Hash]",
                                          show_byte_progress=True)
        self.hash_seconds = time.time() - t_h0

        for size, filepaths in candidate_groups:
            hash_groups = defaultdict(list)
            for fp in filepaths:
                digest = full_hashes.get(fp)
                if digest:
                    hash_groups[digest].append(fp)

            # Collect groups with actual duplicates
            for file_hash, fps in _iteritems(hash_groups):
                if len(fps) > 1:
                    duplicates.append({
                        'hash': file_hash,
                        'size': size,
                        'files': sorted(fps)
                    })

        # Clear progress line when done
        self.progress.clear()

        # Sort by size (largest first) for more impactful results at top
        duplicates.sort(key=lambda x: x['size'], reverse=True)

        return duplicates


# ============================================================================
# Output Functions
# ============================================================================

def format_report(finder, duplicates):
    """Format the duplicate report as a string."""
    lines = []

    # Header
    lines.append("=" * 70)
    lines.append("DUPLICATE FILE REPORT")
    lines.append("=" * 70)
    lines.append("")

    # Statistics
    lines.append("SCAN STATISTICS:")
    lines.append("-" * 40)
    lines.append("  Directories scanned:    {0}".format(
        finder.dirs_scanned))
    lines.append("  Total files scanned:    {0}".format(
        finder.total_files_scanned))
    lines.append("  Total bytes scanned:    {0} ({1})".format(
        finder.total_bytes_scanned,
        human_readable_size(finder.total_bytes_scanned)))
    lines.append("  Files skipped (small):  {0}".format(
        finder.files_skipped_size))
    lines.append("  Files skipped (hardlink/dup path): {0}".format(
        finder.files_skipped_inode))
    lines.append("  Files skipped (error):  {0}".format(
        finder.files_skipped_error))
    lines.append("  Directories skipped:    {0}".format(
        finder.dirs_skipped))
    lines.append("  Scan time (walk):       {0}".format(
        human_readable_duration(finder.scan_total_seconds)))
    lines.append("  Pre-filter time:        {0}".format(
        human_readable_duration(finder.prefilter_seconds)))
    lines.append("  Full-hash time:         {0}".format(
        human_readable_duration(finder.hash_seconds)))
    lines.append("")

    # Duplicate summary
    if not duplicates:
        lines.append("No duplicate files found!")
        lines.append("")
        return "\n".join(lines)

    total_dup_groups = len(duplicates)
    total_dup_files = sum(len(d['files']) for d in duplicates)
    total_wasted = sum(d['size'] * (len(d['files']) - 1) for d in duplicates)

    lines.append("DUPLICATE SUMMARY:")
    lines.append("-" * 40)
    lines.append("  Duplicate groups found: {0}".format(total_dup_groups))
    lines.append("  Total duplicate files:  {0}".format(total_dup_files))
    lines.append("  Wasted space:           {0} ({1})".format(
        total_wasted, human_readable_size(total_wasted)))
    lines.append("  Note: on ZFS (QuTS hero) or btrfs reflinks, identical files")
    lines.append("        may already share blocks, so actual reclaimable space")
    lines.append("        can be lower than shown.")
    lines.append("")

    # Detailed duplicate listing
    lines.append("DUPLICATE FILES (largest first):")
    lines.append("=" * 70)

    for i, dup in enumerate(duplicates, 1):
        lines.append("")
        lines.append("Group {0}: {1} ({2} files)".format(
            i,
            human_readable_size(dup['size']),
            len(dup['files'])))
        lines.append("  MD5: {0}".format(dup['hash']))
        lines.append("  Files:")
        for fp in dup['files']:
            lines.append("    - {0}".format(fp))

    lines.append("")
    lines.append("=" * 70)
    lines.append("END OF REPORT")
    lines.append("=" * 70)

    return "\n".join(lines)


def print_report(report_text, output_file=None):
    """Print report to console and optionally save to file."""
    print(report_text)

    if output_file:
        try:
            # Binary mode: paths are opaque bytes (possibly non-ASCII media
            # filenames), so write them through verbatim without inviting any
            # implicit codec into the path. On Py3 report_text is already text,
            # so encode it (matches save_plan/write_delete_script).
            with open(output_file, 'wb') as f:
                f.write(report_text.encode('utf-8')
                        if isinstance(report_text, type(u'')) else report_text)
            print("")
            print("Report saved to: {0}".format(output_file))
        except (IOError, OSError) as e:
            print("Error saving report: {0}".format(e), file=sys.stderr)


# ============================================================================
# Deletion
# ============================================================================

def _safe_mtime(path, on_error=float('inf')):
    """
    Modification time, or `on_error` if it can't be read.

    The sentinel is direction-aware: the 'oldest' branch passes +inf so an
    unreadable file is never the minimum, and the 'newest' branch passes -inf
    so it's never the maximum. Either way an unreadable file is never chosen as
    the survivor (every readable copy is preferred), which is the safe default.
    """
    try:
        return os.path.getmtime(path)
    except (OSError, IOError):
        return on_error


def select_survivor(dup, strategy):
    """
    Decide which single file in a duplicate group to KEEP.

    Returns (keep_path, [delete_paths]). Exactly one file is ever kept, so the
    delete list can never contain every copy.

    Strategies:
      oldest   - earliest mtime (the likely original)        [default]
      newest   - latest mtime
      shortest - shortest full path (fewest/!shorter dirs)
      first    - first in sorted order (files are pre-sorted)
    Ties are broken deterministically by (shorter path, then alphabetical).
    """
    files = dup['files']

    # files is pre-sorted alphabetically, and min()/max() return the FIRST
    # extremal element — so ties fall back to the alphabetically-first path
    # deterministically without extra tie-break keys.
    if strategy == 'first':
        keep = files[0]
    elif strategy == 'shortest':
        keep = min(files, key=lambda p: (len(p), p))
    elif strategy == 'newest':
        # -inf so an unreadable mtime sorts as oldest, never the kept maximum.
        keep = max(files, key=lambda p: (_safe_mtime(p, float('-inf')), -len(p)))
    else:  # 'oldest' (default)
        keep = min(files, key=lambda p: (_safe_mtime(p), len(p)))

    deletes = [f for f in files if f != keep]
    return keep, deletes


def plan_deletions(duplicates, strategy):
    """
    Build a deletion plan from the duplicate groups.

    Returns a list of {'keep', 'deletes', 'size', 'hash'} — one per group that
    has something to delete. Invariant: 'deletes' is non-empty and the group's
    surviving copy ('keep') is never in it.
    """
    plan = []
    for dup in duplicates:
        keep, deletes = select_survivor(dup, strategy)
        if not deletes:
            continue
        plan.append({
            'keep': keep,
            'deletes': deletes,
            'size': dup['size'],
            'hash': dup['hash'],
        })
    return plan


def format_deletion_plan(plan, strategy):
    """Human-readable text of what would be deleted (used for the dry run)."""
    lines = []
    lines.append("")
    lines.append("=" * 70)
    lines.append("DELETION PLAN (keep strategy: {0})".format(strategy))
    lines.append("=" * 70)

    total_files = 0
    total_reclaim = 0
    for i, item in enumerate(plan, 1):
        total_files += len(item['deletes'])
        total_reclaim += item['size'] * len(item['deletes'])
        lines.append("")
        lines.append("Group {0}: {1} each, deleting {2} of {3} copies".format(
            i,
            human_readable_size(item['size']),
            len(item['deletes']),
            len(item['deletes']) + 1))
        lines.append("  KEEP:   {0}".format(item['keep']))
        for fp in item['deletes']:
            lines.append("  DELETE: {0}".format(fp))

    lines.append("")
    lines.append("-" * 70)
    lines.append("Groups with deletions:  {0}".format(len(plan)))
    lines.append("Files to delete:        {0}".format(total_files))
    lines.append("Space to reclaim:       {0} ({1})".format(
        total_reclaim, human_readable_size(total_reclaim)))
    lines.append("(On ZFS/QuTS hero or btrfs reflinks, copies may already share")
    lines.append(" blocks, so actual reclaimed space can be lower.)")
    lines.append("-" * 70)
    return "\n".join(lines)


def _shell_quote(path):
    """Single-quote a path for /bin/sh, escaping any embedded single quotes."""
    return "'" + path.replace("'", "'\\''") + "'"


def write_delete_script(plan, filename):
    """
    Write a reviewable shell script of `rm` commands (does NOT delete anything).
    The kept file is shown as a comment above each group's removals.
    """
    out = []
    out.append("#!/bin/sh")
    out.append("# Duplicate-deletion script generated by find_duplicates.py")
    out.append("# Review carefully, then run with:  sh {0}".format(filename))
    out.append("# Each group keeps ONE file (shown as '# KEEP:') and removes the rest.")
    out.append("")

    total_files = 0
    total_reclaim = 0
    for i, item in enumerate(plan, 1):
        total_files += len(item['deletes'])
        total_reclaim += item['size'] * len(item['deletes'])
        out.append("# Group {0}: {1} each".format(
            i, human_readable_size(item['size'])))
        out.append("# KEEP: {0}".format(item['keep']))
        for fp in item['deletes']:
            out.append("rm -- {0}".format(_shell_quote(fp)))
        out.append("")

    out.append("# Total: {0} files, {1} reclaimable".format(
        total_files, human_readable_size(total_reclaim)))
    out.append("")

    text = "\n".join(out)
    # Binary mode: paths are opaque bytes, write them through verbatim.
    with open(filename, 'wb') as f:
        f.write(text.encode('utf-8') if isinstance(text, type(u'')) else text)


def apply_deletions(plan, verify=False, show_progress=True):
    """
    Actually delete the non-survivor files.

    Safety: if a group's survivor no longer exists at delete time, the whole
    group is skipped (so we never leave zero copies). With verify=True, each
    candidate is re-hashed and only deleted if it still matches the survivor's
    recorded hash.

    Returns {'deleted', 'failed', 'reclaimed'}.
    """
    progress = ProgressDisplay(enabled=show_progress)
    total = sum(len(item['deletes']) for item in plan)
    done = 0
    deleted = 0
    failed = 0
    reclaimed = 0

    for item in plan:
        # Never delete if the keeper vanished since the scan.
        if not os.path.exists(item['keep']):
            progress.clear()
            print("Skipping group (survivor missing): {0}".format(item['keep']),
                  file=sys.stderr)
            done += len(item['deletes'])
            continue

        for fp in item['deletes']:
            done += 1
            name = os.path.basename(fp)
            if len(name) > 30:
                name = name[:27] + '...'
            progress.update("[Delete] {0}/{1} | reclaimed {2} | {3}".format(
                done, total, human_readable_size(reclaimed), name))

            if verify:
                current = compute_md5(fp)
                if current != item['hash']:
                    progress.clear()
                    print("Skipping (content changed since scan): {0}".format(fp),
                          file=sys.stderr)
                    failed += 1
                    continue

            try:
                os.remove(fp)
                deleted += 1
                reclaimed += item['size']
            except (OSError, IOError) as e:
                progress.clear()
                print("Failed to delete {0}: {1}".format(fp, e), file=sys.stderr)
                failed += 1

    progress.clear()
    return {'deleted': deleted, 'failed': failed, 'reclaimed': reclaimed}


# ============================================================================
# Plan persistence (save a dry-run plan, re-apply it without re-scanning)
# ============================================================================

PLAN_VERSION = 1
_PY2 = bytes is str  # On Python 2, filesystem paths are bytes (str).


def _path_to_text(p):
    """Filesystem path -> JSON-safe text. Lossless via latin-1 on Py2 bytes."""
    if _PY2 and isinstance(p, bytes):
        # latin-1 round-trips every byte; pure-ASCII names stay readable.
        return p.decode('latin-1')
    return p  # Python 3 paths from os.walk are already str


def _text_to_path(s):
    """JSON text -> filesystem path of the OS-native type."""
    if _PY2 and isinstance(s, unicode):  # noqa: F821 (py2 only)
        return s.encode('latin-1')
    return s


def save_plan(plan, filename, strategy, min_size):
    """Serialize a deletion plan to JSON so --apply can reuse it without scanning."""
    total_files = sum(len(item['deletes']) for item in plan)
    total_reclaim = sum(item['size'] * len(item['deletes']) for item in plan)
    data = {
        'tool': 'find_duplicates.py',
        'version': PLAN_VERSION,
        'created': time.strftime('%Y-%m-%d %H:%M:%S'),
        'strategy': strategy,
        'min_size': min_size,
        'summary': {
            'groups': len(plan),
            'files_to_delete': total_files,
            'reclaim_bytes': total_reclaim,
        },
        'groups': [
            {
                'hash': item['hash'],
                'size': item['size'],
                'keep': _path_to_text(item['keep']),
                'deletes': [_path_to_text(p) for p in item['deletes']],
            }
            for item in plan
        ],
    }
    text = json.dumps(data, indent=2)
    # ensure_ascii (default) keeps the body ASCII; write bytes for Py2/Py3 parity.
    with open(filename, 'wb') as f:
        f.write(text if isinstance(text, bytes) else text.encode('utf-8'))


def load_plan(filename):
    """
    Load a JSON plan. Returns (groups, metadata) where groups is the same shape
    apply_deletions() expects ({'keep','deletes','size','hash'}) with OS-native
    paths. Raises ValueError on a malformed/incompatible file.
    """
    with open(filename, 'rb') as f:
        raw = f.read()
    data = json.loads(raw.decode('utf-8'))

    if not isinstance(data, dict) or data.get('tool') != 'find_duplicates.py':
        raise ValueError("not a find_duplicates.py plan file")
    if data.get('version') != PLAN_VERSION:
        raise ValueError("unsupported plan version: {0}".format(data.get('version')))

    groups = []
    for g in data.get('groups', []):
        keep = _text_to_path(g['keep'])
        deletes = [_text_to_path(p) for p in g['deletes']]
        # Defensive: a hand-edited plan must never list the survivor among the
        # deletes (that would delete the only copy). Drop it, then skip the
        # group if nothing is left to delete.
        deletes = [p for p in deletes if p != keep]
        if not deletes:
            continue
        groups.append({
            'hash': g.get('hash', ''),
            'size': int(g.get('size', 0)),
            'keep': keep,
            'deletes': deletes,
        })
    if not groups:
        raise ValueError("plan contains no deletions")

    return groups, data


# ============================================================================
# Main Entry Point
# ============================================================================

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Find duplicate files by size and MD5 hash.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia
  python find_duplicates.py /path1 /path2 --output report.txt
  python find_duplicates.py /path --min-size 10485760 --verbose
        """
    )

    parser.add_argument(
        'paths',
        nargs='*',
        help='One or more directory paths to scan '
             '(not required with --from-plan)'
    )

    parser.add_argument(
        '-o', '--output',
        metavar='FILE',
        help='Save report to a text file'
    )

    parser.add_argument(
        '-m', '--min-size',
        type=int,
        default=DEFAULT_MIN_SIZE,
        metavar='BYTES',
        help='Minimum file size in bytes (default: 1MB = 1048576)'
    )

    parser.add_argument(
        '-j', '--workers',
        type=int,
        default=1,
        metavar='N',
        help='Number of parallel hashing threads (default: 1). The full-hash '
             'phase does large sequential reads, and on a spinning-disk array '
             '(e.g. HDD RAID-6) a single reader already saturates it -- more '
             'threads just cause seek thrashing and go slower. Bump it (2-8) '
             'only on SSD/NVMe arrays or other setups where parallel reads help.'
    )

    parser.add_argument(
        '--scan-workers',
        type=int,
        default=8,
        metavar='N',
        help='Number of parallel threads for the directory-scan/stat walk '
             '(default: 8). The scan is metadata-bound (stat/listdir latency), '
             'not bandwidth-bound, so it tolerates more concurrency than '
             '--workers; raise it (e.g. 16) on an idle multi-disk array.'
    )

    parser.add_argument(
        '--include-metadata',
        action='store_true',
        help='Include QNAP metadata directories (starting with @)'
    )

    parser.add_argument(
        '--follow-symlinks',
        action='store_true',
        help='Follow symbolic links (default: skip symlinks)'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Show progress during scan'
    )

    parser.add_argument(
        '--no-progress',
        action='store_true',
        help='Disable the progress indicator'
    )

    parser.add_argument(
        '--zero-exit',
        action='store_true',
        help='Always exit 0, even when duplicates are found '
             '(default: exit 1 when duplicates are found)'
    )

    # --- Deletion options ---------------------------------------------------
    delete_group = parser.add_argument_group('deletion')
    delete_group.add_argument(
        '--delete',
        action='store_true',
        help='Plan deletion of duplicates (keeps one copy per group). '
             'Without --apply or --delete-script this is a DRY RUN.'
    )
    delete_group.add_argument(
        '--keep',
        choices=['oldest', 'newest', 'shortest', 'first'],
        default='oldest',
        help='Which copy to keep in each group (default: oldest by mtime). '
             'shortest=shortest path, first=first alphabetically.'
    )
    delete_group.add_argument(
        '--apply',
        action='store_true',
        help='With --delete, actually delete the files (prompts unless --yes). '
             'Hardlinks/symlinks are never split: the scan already de-dups by inode.'
    )
    delete_group.add_argument(
        '--delete-script',
        metavar='FILE',
        help='With --delete, write an rm-command shell script to FILE for review '
             '(deletes nothing itself)'
    )
    delete_group.add_argument(
        '--verify',
        action='store_true',
        help='With --apply, re-hash each file right before deleting and skip it '
             'if its content changed since the scan'
    )
    delete_group.add_argument(
        '--yes',
        action='store_true',
        help='With --apply, skip the confirmation prompt (for cron/automation)'
    )
    delete_group.add_argument(
        '--save-plan',
        metavar='FILE',
        help='Write the deletion plan to FILE as JSON (default: auto-saved to a '
             'timestamped file on a dry run, for reuse with --from-plan)'
    )
    delete_group.add_argument(
        '--no-save-plan',
        action='store_true',
        help='Do not auto-save the JSON plan on a dry run'
    )
    delete_group.add_argument(
        '--from-plan',
        metavar='FILE',
        help='Apply a previously-saved JSON plan WITHOUT re-scanning. Combine '
             'with --apply to delete (and --verify is recommended for old plans)'
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Apply a previously-saved plan without re-scanning, then we're done.
    if args.from_plan:
        stats = apply_from_plan(args)
        # Exit 1 only if an apply actually ran and some deletions failed;
        # a dry-run preview or a clean apply exits 0 (good for cron).
        if stats is not None and stats['failed']:
            sys.exit(1)
        sys.exit(0)

    # Validate paths
    valid_paths = []
    for p in args.paths:
        if os.path.isdir(p):
            valid_paths.append(p)
        else:
            print("Warning: Skipping invalid path: {0}".format(p),
                  file=sys.stderr)

    if not valid_paths:
        print("Error: No valid directories to scan (or use --from-plan).",
              file=sys.stderr)
        sys.exit(2)

    if args.min_size < 1:
        print("Note: --min-size below 1 byte is treated as 1; zero-byte files "
              "are always skipped.", file=sys.stderr)

    # Create finder and scan
    finder = DuplicateFinder(
        min_size=args.min_size,
        include_metadata=args.include_metadata,
        follow_symlinks=args.follow_symlinks,
        verbose=args.verbose,
        show_progress=not args.no_progress,
        workers=args.workers,
        scan_workers=args.scan_workers
    )

    print("Starting scan...")
    print("Minimum file size: {0}".format(human_readable_size(args.min_size)))
    print("")

    # Phase 1: Scan and group by size
    finder.scan_directories(valid_paths)

    if finder.total_files_scanned == 0:
        print("No files found matching criteria.")
        sys.exit(0)

    # Phase 2: Find duplicates by hashing
    duplicates = finder.find_duplicates()

    # Generate and output report
    report = format_report(finder, duplicates)
    print_report(report, args.output)

    # Phase 3 (optional): deletion
    applied = None
    if args.delete and duplicates:
        applied = run_deletion(duplicates, args)

    # Exit code:
    #   0 = no duplicates found (or --zero-exit), or a deletion completed cleanly
    #   1 = duplicates found on a scan-only run, or some deletions failed
    #   2 = usage error (no valid paths) -- handled above
    # When --apply actually deleted, the exit code reflects the apply result
    # (so a successful cron delete reads as success) rather than the mere
    # presence of duplicates.
    if applied is not None:
        sys.exit(1 if applied['failed'] else 0)
    if duplicates and not args.zero_exit:
        sys.exit(1)
    sys.exit(0)


def _default_plan_name():
    """Timestamped default filename for an auto-saved dry-run plan."""
    # Include the PID so two runs in the same second don't collide (strftime
    # has only 1s granularity).
    return "find_dupes_plan_{0}_{1}.json".format(
        time.strftime("%Y%m%d_%H%M%S"), os.getpid())


def _confirm_and_apply(plan, args):
    """
    Shared confirm + delete + summary for both fresh and from-plan applies.

    Returns the apply_deletions stats dict, or None if the user aborted at the
    confirmation prompt (so callers can distinguish "nothing happened" from a
    completed apply when choosing an exit code).
    """
    total_files = sum(len(item['deletes']) for item in plan)
    total_reclaim = sum(item['size'] * len(item['deletes']) for item in plan)

    if not args.yes:
        prompt = "\nDelete {0} files, reclaiming {1}? [y/N] ".format(
            total_files, human_readable_size(total_reclaim))
        try:
            answer = raw_input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ''
        if answer not in ('y', 'yes'):
            print("Aborted. Nothing deleted.")
            return None

    stats = apply_deletions(plan, verify=args.verify,
                            show_progress=not args.no_progress)
    print("\nDeletion complete:")
    print("  Deleted:   {0}".format(stats['deleted']))
    print("  Failed:    {0}".format(stats['failed']))
    print("  Reclaimed: {0} ({1})".format(
        stats['reclaimed'], human_readable_size(stats['reclaimed'])))
    return stats


def run_deletion(duplicates, args):
    """
    Build a deletion plan and either preview (saving it), export, or apply it.

    Returns the apply stats dict when --apply actually deleted (so main() can
    base its exit code on the failure count), else None (dry run / script export
    / nothing to delete / aborted).
    """
    plan = plan_deletions(duplicates, args.keep)
    if not plan:
        print("\nNothing to delete (every group has only one copy).")
        return None

    print(format_deletion_plan(plan, args.keep))

    # Mode 1: export a reviewable rm-script (deletes nothing).
    if args.delete_script:
        try:
            write_delete_script(plan, args.delete_script)
            print("\nDeletion script written to: {0}".format(args.delete_script))
            print("Review it, then run:  sh {0}".format(args.delete_script))
        except (IOError, OSError) as e:
            print("Error writing script: {0}".format(e), file=sys.stderr)
        return None

    # Mode 2: actually delete. Save the plan first if explicitly requested.
    if args.apply:
        if args.save_plan:
            try:
                save_plan(plan, args.save_plan, args.keep, args.min_size)
                print("\nPlan saved to: {0}".format(args.save_plan))
            except (IOError, OSError) as e:
                print("Warning: could not save plan: {0}".format(e), file=sys.stderr)
        return _confirm_and_apply(plan, args)

    # Mode 3: dry run — save the plan so --apply can reuse it without re-scanning.
    plan_path = args.save_plan
    if not plan_path and not args.no_save_plan:
        plan_path = _default_plan_name()
    if plan_path:
        try:
            save_plan(plan, plan_path, args.keep, args.min_size)
            print("\nPlan saved to: {0}".format(plan_path))
            print("Apply later WITHOUT re-scanning:")
            print("  python find_duplicates.py --from-plan {0} --apply".format(
                _shell_quote(plan_path)))
        except (IOError, OSError) as e:
            print("Warning: could not save plan: {0}".format(e), file=sys.stderr)

    print("\nDRY RUN — nothing deleted.")
    print("Re-run with --apply to delete now, or --delete-script FILE for an rm-script.")


def apply_from_plan(args):
    """
    Load a saved JSON plan and preview or apply it, with no scanning.

    Returns the apply stats dict when --apply actually deleted, else None
    (dry-run preview or aborted), so main() can set the exit code accordingly.
    """
    try:
        groups, meta = load_plan(args.from_plan)
    except (IOError, OSError) as e:
        print("Error: cannot read plan {0}: {1}".format(args.from_plan, e),
              file=sys.stderr)
        sys.exit(2)
    except ValueError as e:
        print("Error: invalid plan {0}: {1}".format(args.from_plan, e),
              file=sys.stderr)
        sys.exit(2)

    strategy = meta.get('strategy', '?')
    created = meta.get('created', '?')
    print("Loaded plan from {0} (created {1}, keep strategy: {2})".format(
        args.from_plan, created, strategy))
    print(format_deletion_plan(groups, strategy))

    if not args.apply:
        print("\nDRY RUN — nothing deleted. Re-run with --apply to delete.")
        return None

    if not args.verify:
        print("\nNote: applying a saved plan without --verify. If files may have "
              "changed since the plan was made, re-run with --verify.",
              file=sys.stderr)
    return _confirm_and_apply(groups, args)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        # Scan/hash run in daemon threads, so they're torn down on exit. The
        # scan and dry-run phases touch nothing on disk; only --apply deletes,
        # and that runs single-threaded in the main thread.
        print("\nInterrupted by user (Ctrl+C). Aborting.", file=sys.stderr)
        sys.exit(130)
