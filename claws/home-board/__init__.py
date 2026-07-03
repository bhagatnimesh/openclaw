from _unittest_discovery import load_local_tests


def load_tests(loader, tests, pattern):
    return load_local_tests(loader, __file__, pattern)
