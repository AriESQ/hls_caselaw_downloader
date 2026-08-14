"""Named process exit codes.

A future CI step can branch on these without parsing log text.
"""

OK = 0
PARTIAL_FAILURE = 1  # run completed but some files failed after retries
CONFIG_ERROR = 2  # bad config/selection, nothing was attempted
FATAL = 3  # unexpected error
