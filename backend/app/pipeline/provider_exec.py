"""Apply an output-file bound before exec; argv is configuration, never user input."""
import os
import resource
import sys

if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 * 1024, 2 * 1024 * 1024))
    os.execv(sys.argv[1], sys.argv[1:])
