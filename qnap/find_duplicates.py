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
import time
from collections import defaultdict

# raw_input was renamed to input in Python 3. Keep the 2.7 name working while
# also letting the script run unmodified under Python 3 (used for testing the
# deletion logic off-NAS).
try:
    raw_input
except NameError:
    raw_input = input

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
                 follow_symlinks=False, verbose=False, show_progress=True):
        self.min_size = min_size
        self.include_metadata = include_metadata
        self.follow_symlinks = follow_symlinks
        self.verbose = verbose
        self.progress = ProgressDisplay(enabled=show_progress)

        # Statistics
        self.total_files_scanned = 0
        self.total_bytes_scanned = 0
        self.files_skipped_size = 0
        self.files_skipped_error = 0
        self.files_skipped_inode = 0
        self.dirs_scanned = 0
        self.dirs_skipped = 0

        # Phase 1: Group files by size
        # {size_in_bytes: [filepath1, filepath2, ...]}
        self.size_groups = defaultdict(list)

        # (st_dev, st_ino) pairs already recorded. This makes the scan
        # idempotent across overlapping input paths (e.g. /a and /a/b given
        # together) and prevents hardlinks to the same inode from being
        # reported as reclaimable duplicates -- deleting one frees nothing.
        self._seen_inodes = set()

    def log(self, message):
        """Print message if verbose mode is enabled."""
        if self.verbose:
            self.progress.clear()
            print(message, file=sys.stderr)

    def _update_scan_progress(self, current_dir):
        """Update the scanning progress display."""
        # Shorten directory path for display
        display_dir = current_dir
        if len(display_dir) > 40:
            display_dir = '...' + display_dir[-37:]

        msg = "[Scan] Files: {0} | Size: {1} | Dirs: {2} | {3}".format(
            self.total_files_scanned,
            human_readable_size(self.total_bytes_scanned),
            self.dirs_scanned,
            display_dir
        )
        self.progress.update(msg)

    def scan_directories(self, paths):
        """
        Scan one or more directory paths for files.

        Phase 1: Groups all files by size.
        """
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

            self.log("Scanning: {0}".format(base_path))
            self._scan_directory(base_path)

        # Clear progress line when done
        self.progress.clear()

    def _scan_directory(self, base_path):
        """Recursively scan a single directory."""
        for root, dirs, files in os.walk(base_path,
                                          followlinks=self.follow_symlinks):
            self.dirs_scanned += 1
            self._update_scan_progress(root)

            # Filter out directories we should skip (modifies in-place)
            original_dir_count = len(dirs)
            dirs[:] = [d for d in dirs
                       if not should_skip_dir(d, self.include_metadata)]
            self.dirs_skipped += original_dir_count - len(dirs)

            # Process files
            for filename in files:
                filepath = os.path.join(root, filename)

                # Skip symlinks unless explicitly following them
                if not self.follow_symlinks and os.path.islink(filepath):
                    continue

                try:
                    # Get file size
                    stat_info = os.stat(filepath)
                    file_size = stat_info.st_size

                    # Skip files below minimum size
                    if file_size < self.min_size:
                        self.files_skipped_size += 1
                        continue

                    # Inode de-duplication. st_ino == 0 means the filesystem
                    # does not report a usable inode number, so fall back to
                    # treating the path as unique rather than collapsing
                    # unrelated files together.
                    if stat_info.st_ino != 0:
                        inode_key = (stat_info.st_dev, stat_info.st_ino)
                        if inode_key in self._seen_inodes:
                            self.files_skipped_inode += 1
                            continue
                        self._seen_inodes.add(inode_key)

                    # Track statistics
                    self.total_files_scanned += 1
                    self.total_bytes_scanned += file_size

                    # Group by size
                    self.size_groups[file_size].append(filepath)

                    # Update progress periodically
                    if self.total_files_scanned % 50 == 0:
                        self._update_scan_progress(root)

                except (OSError, IOError):
                    self.files_skipped_error += 1

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
            (size, files) for size, files in self.size_groups.items()
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
        print("  Unique file sizes found:    {0}".format(len(self.size_groups)))
        print("  Size groups with 2+ files:  {0}".format(len(size_groups_to_check)))
        print("  Candidate files (max):      {0}".format(upper_bound_files))
        print("  Data to read (max):         {0}".format(
            human_readable_size(upper_bound_bytes)))
        print("-" * 50)
        print("")

        if upper_bound_files == 0:
            print("No potential duplicates found - nothing to hash.")
            return duplicates

        # ------------------------------------------------------------------
        # Phase 2a: pre-filter with a cheap head+tail partial hash.
        #
        # We build the definitive list of files that still need a full hash
        # FIRST, so the progress denominators below are accurate. The original
        # computed them from the full size-group counts, so the percentage and
        # the "File N/total" counter never reached 100% whenever the pre-filter
        # eliminated anything.
        # ------------------------------------------------------------------
        candidate_groups = []  # list of (size, [filepaths sharing a partial hash])

        for size, filepaths in size_groups_to_check:
            if len(filepaths) > 2:
                partial_groups = defaultdict(list)
                for idx, fp in enumerate(filepaths):
                    display_fp = os.path.basename(fp)
                    if len(display_fp) > 30:
                        display_fp = display_fp[:27] + '...'
                    msg = "[Pre-filter] Group size {0} | {1}/{2} | {3}".format(
                        human_readable_size(size),
                        idx + 1,
                        len(filepaths),
                        display_fp
                    )
                    self.progress.update(msg)

                    partial = compute_partial_md5(fp)
                    if partial is not None:
                        partial_groups[partial].append(fp)
                    else:
                        self.files_skipped_error += 1

                for fps in partial_groups.values():
                    if len(fps) > 1:
                        candidate_groups.append((size, fps))
            else:
                candidate_groups.append((size, filepaths))

        # Accurate denominators for the full-hash pass.
        files_to_hash = sum(len(fps) for _, fps in candidate_groups)
        total_bytes_to_hash = sum(size * len(fps)
                                  for size, fps in candidate_groups)

        self.progress.clear()
        self.log("Pre-filter survivors: {0} files in {1} groups".format(
            files_to_hash, len(candidate_groups)))

        if files_to_hash == 0:
            print("No duplicates survived pre-filtering.")
            return duplicates

        # ------------------------------------------------------------------
        # Phase 2b: full MD5 hash of the survivors.
        # ------------------------------------------------------------------
        hashed_count = 0
        bytes_hashed = 0

        for size, filepaths in candidate_groups:
            hash_groups = defaultdict(list)
            for fp in filepaths:
                hashed_count += 1

                # Shorten filepath for display
                display_fp = os.path.basename(fp)
                if len(display_fp) > 25:
                    display_fp = display_fp[:22] + '...'

                # Create progress callback for this file
                def make_hash_progress(file_num, total_files, filename,
                                        prev_bytes, total_bytes_all):
                    def callback(bytes_read, file_total):
                        current_total = prev_bytes + bytes_read
                        pct = (current_total * 100) // total_bytes_all if total_bytes_all > 0 else 0
                        file_pct = (bytes_read * 100) // file_total if file_total > 0 else 100

                        msg = "[Hash] {0}% | File {1}/{2}: {3}% | {4}".format(
                            pct,
                            file_num,
                            total_files,
                            file_pct,
                            filename
                        )
                        self.progress.update(msg)
                    return callback

                progress_cb = make_hash_progress(
                    hashed_count, files_to_hash, display_fp,
                    bytes_hashed, total_bytes_to_hash
                )

                # Show initial progress for this file
                msg = "[Hash] {0}% | File {1}/{2}: 0% | {3}".format(
                    (bytes_hashed * 100) // total_bytes_to_hash if total_bytes_to_hash > 0 else 0,
                    hashed_count,
                    files_to_hash,
                    display_fp
                )
                self.progress.update(msg, force=True)

                file_hash = compute_md5(fp, progress_callback=progress_cb)
                bytes_hashed += size

                if file_hash:
                    hash_groups[file_hash].append(fp)

            # Collect groups with actual duplicates
            for file_hash, fps in hash_groups.items():
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
            # implicit codec into the path.
            with open(output_file, 'wb') as f:
                f.write(report_text)
            print("")
            print("Report saved to: {0}".format(output_file))
        except (IOError, OSError) as e:
            print("Error saving report: {0}".format(e), file=sys.stderr)


# ============================================================================
# Deletion
# ============================================================================

def _safe_mtime(path):
    """Modification time, or +inf if it can't be read (so it's never 'oldest')."""
    try:
        return os.path.getmtime(path)
    except (OSError, IOError):
        return float('inf')


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
        keep = max(files, key=lambda p: (_safe_mtime(p), -len(p)))
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
        nargs='+',
        help='One or more directory paths to scan'
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

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Validate paths
    valid_paths = []
    for p in args.paths:
        if os.path.isdir(p):
            valid_paths.append(p)
        else:
            print("Warning: Skipping invalid path: {0}".format(p),
                  file=sys.stderr)

    if not valid_paths:
        print("Error: No valid directories to scan.", file=sys.stderr)
        sys.exit(2)

    # Create finder and scan
    finder = DuplicateFinder(
        min_size=args.min_size,
        include_metadata=args.include_metadata,
        follow_symlinks=args.follow_symlinks,
        verbose=args.verbose,
        show_progress=not args.no_progress
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
    if args.delete and duplicates:
        run_deletion(duplicates, args)

    # Exit code:
    #   0 = no duplicates found (or --zero-exit given)
    #   1 = duplicates found (useful as a cron/script signal)
    #   2 = usage error (no valid paths) -- handled above
    if duplicates and not args.zero_exit:
        sys.exit(1)
    sys.exit(0)


def run_deletion(duplicates, args):
    """Build a deletion plan and either preview, export, or apply it."""
    plan = plan_deletions(duplicates, args.keep)
    if not plan:
        print("\nNothing to delete (every group has only one copy).")
        return

    print(format_deletion_plan(plan, args.keep))

    total_files = sum(len(item['deletes']) for item in plan)
    total_reclaim = sum(item['size'] * len(item['deletes']) for item in plan)

    # Mode 1: export a reviewable rm-script (deletes nothing).
    if args.delete_script:
        try:
            write_delete_script(plan, args.delete_script)
            print("\nDeletion script written to: {0}".format(args.delete_script))
            print("Review it, then run:  sh {0}".format(args.delete_script))
        except (IOError, OSError) as e:
            print("Error writing script: {0}".format(e), file=sys.stderr)
        return

    # Mode 2: actually delete.
    if args.apply:
        if not args.yes:
            prompt = "\nDelete {0} files, reclaiming {1}? [y/N] ".format(
                total_files, human_readable_size(total_reclaim))
            try:
                answer = raw_input(prompt).strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ''
            if answer not in ('y', 'yes'):
                print("Aborted. Nothing deleted.")
                return

        stats = apply_deletions(plan, verify=args.verify,
                                show_progress=not args.no_progress)
        print("\nDeletion complete:")
        print("  Deleted:   {0}".format(stats['deleted']))
        print("  Failed:    {0}".format(stats['failed']))
        print("  Reclaimed: {0} ({1})".format(
            stats['reclaimed'], human_readable_size(stats['reclaimed'])))
        return

    # Mode 3: dry run (default).
    print("\nDRY RUN — nothing deleted.")
    print("Re-run with --apply to delete, or --delete-script FILE to export "
          "an rm-script.")


if __name__ == '__main__':
    main()
