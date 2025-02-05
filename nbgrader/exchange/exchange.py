import os
import datetime
import sys
import shutil
import glob

from textwrap import dedent

from dateutil.tz import gettz
from traitlets.config import LoggingConfigurable
from traitlets import Unicode, Bool, Instance, default, validate
from jupyter_core.paths import jupyter_data_dir

from ..utils import check_directory, ignore_patterns, self_owned
from ..coursedir import CourseDirectory


class ExchangeError(Exception):
    pass


class Exchange(LoggingConfigurable):

    course_id = Unicode(
        '',
        help=dedent(
            """
            A key that is unique per instructor and course. This MUST be
            specified, either by setting the config option, or using the
            --course option on the command line.
            """
        )
    ).tag(config=True)

    assignment_dir = Unicode(
        ".",
        help=dedent(
            """
            Local path for storing student assignments.  Defaults to '.'
            which is normally Jupyter's notebook_dir.
            """
        )
    ).tag(config=True)


    @validate('course_id')
    def _validate_course_id(self, proposal):
        if proposal['value'].strip() != proposal['value']:
            self.log.warning("course_id '%s' has trailing whitespace, stripping it away", proposal['value'])
        return proposal['value'].strip()

    timezone = Unicode(
        "UTC",
        help="Timezone for recording timestamps"
    ).tag(config=True)

    timestamp_format = Unicode(
        "%Y-%m-%d %H:%M:%S.%f %Z",
        help="Format string for timestamps"
    ).tag(config=True)

    root = Unicode(
        "/srv/nbgrader/exchange",
        help="The nbgrader exchange directory writable to everyone. MUST be preexisting."
    ).tag(config=True)

    cache = Unicode(
        "",
        help="Local cache directory for nbgrader submit and nbgrader list. Defaults to $JUPYTER_DATA_DIR/nbgrader_cache"
    ).tag(config=True)

    @default("cache")
    def _cache_default(self):
        return os.path.join(jupyter_data_dir(), 'nbgrader_cache')

    path_includes_course = Bool(
        False,
        help=dedent(
            """
            Whether the path for fetching/submitting  assignments should be
            prefixed with the course name. If this is `False`, then the path
            will be something like `./ps1`. If this is `True`, then the path
            will be something like `./course123/ps1`.
            """
        )
    ).tag(config=True)

    coursedir = Instance(CourseDirectory, allow_none=True)

    def __init__(self, coursedir=None, **kwargs):
        self.coursedir = coursedir
        super(Exchange, self).__init__(**kwargs)

    def fail(self, msg):
        self.log.fatal(msg)
        raise ExchangeError(msg)

    def set_timestamp(self):
        """Set the timestap using the configured timezone."""
        tz = gettz(self.timezone)
        if tz is None:
            self.fail("Invalid timezone: {}".format(self.timezone))
        self.timestamp = datetime.datetime.now(tz).strftime(self.timestamp_format)

    def set_perms(self, dest, fileperms, dirperms):
        all_dirs = []
        for dirname, _, filenames in os.walk(dest):
            for filename in filenames:
                os.chmod(os.path.join(dirname, filename), fileperms)
            all_dirs.append(dirname)

        for dirname in all_dirs[::-1]:
            os.chmod(dirname, dirperms)

    def ensure_root(self):
        """See if the exchange directory exists and is writable, fail if not."""
        if not check_directory(self.root, write=True, execute=True):
            self.fail("Unwritable directory, please contact your instructor: {}".format(self.root))

    def init_src(self):
        """Compute and check the source paths for the transfer."""
        raise NotImplementedError

    def init_dest(self):
        """Compute and check the destination paths for the transfer."""
        raise NotImplementedError

    def copy_files(self):
        """Actually do the file transfer."""
        raise NotImplementedError

    def do_copy(self, src, dest, log=None):
        """
        Copy the src dir to the dest dir, omitting excluded
        file/directories, non included files, and too large files, as
        specified by the options coursedir.ignore, coursedir.include
        and coursedir.max_file_size.
        """
        shutil.copytree(src, dest,
                        ignore=ignore_patterns(exclude=self.coursedir.ignore,
                                               include=self.coursedir.include,
                                               max_file_size=self.coursedir.max_file_size,
                                               log=self.log))

    def start(self):
        if sys.platform == 'win32':
            self.fail("Sorry, the exchange is not available on Windows.")

        self.ensure_root()
        self.set_timestamp()

        self.init_src()
        self.init_dest()
        self.copy_files()

    def _assignment_not_found(self, src_path, other_path):
        msg = "Assignment not found at: {}".format(src_path)
        self.log.fatal(msg)
        found = glob.glob(other_path)
        if found:
            # Normally it is a bad idea to put imports in the middle of
            # a function, but we do this here because otherwise fuzzywuzzy
            # prints an annoying message about python-Levenshtein every
            # time nbgrader is run.
            from fuzzywuzzy import fuzz
            scores = sorted([(fuzz.ratio(self.src_path, x), x) for x in found])
            self.log.error("Did you mean: %s", scores[-1][1])

        raise ExchangeError(msg)

    def ensure_directory(self, path, mode):
        """Ensure that the path exists, has the right mode and is self owned."""
        if not os.path.isdir(path):
            os.makedirs(path)
            # For some reason, Python won't create a directory with a mode of 0o733
            # so we have to create and then chmod.
            os.chmod(path, mode)
        else:
            if not self_owned(path):
                self.fail("You don't own the directory: {}".format(path))
