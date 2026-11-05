"""Set native-parser limits in a fresh interpreter, before loading native code."""
import os
import resource
import sys

limit = int(sys.argv[1])
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
