
import os
import unittest
import subprocess
import shutil
import tempfile

UPDATE_HOOK = os.path.join(os.path.dirname(__file__), '..', 'update')


class ExecuteException(RuntimeError):
    '''
    Runtime-like exception when execute() fails.
    '''

    def __init__(self, command, exit_code, stdout, stderr):
        self.command = command
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr

    def __str__(self):
        return  'Command {command!r} failed with code {exit_code}: {stderr!r}'.format(command=self.command, exit_code=self.exit_code, stderr=self.stderr)


def execute(command, stdin=None, exception_on_failure=True, cwd=None):
    '''
    Helper function to run a command and return its exit code, standard output and standard error.
    '''

    # Handle single string argument
    if isinstance(command, str):
        command = command.split()

    # Launch subprocess and collect output.
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE, cwd=cwd)
    stdout, stderr = proc.communicate(stdin)
    exit_code = proc.returncode

    if exit_code != 0 and exception_on_failure:
        raise ExecuteException(command, exit_code, stdout, stderr)

    return exit_code, stdout, stderr



class GitRepo():
    '''
    Thin wrapper around a git repo
    '''

    def __init__(self, dir, clone_source=None, options=[]):
        self.dir = dir
        if clone_source:
            if isinstance(clone_source, GitRepo):
                clone_source = clone_source.dir
            execute(['git', 'clone'] + options + [clone_source, dir])
        elif not os.path.exists(dir):
            execute(['git', 'init'] + options + [dir])


    def git(self, *args):
        return execute(['git'] + list(args), cwd=self.dir)

    def editFile(self, filename, content='more content', mode='a', auto_newline=True, auto_add=True):
        with open(os.path.join(self.dir, filename), mode) as f:
            f.write(content)
            if auto_newline:
                f.write('\n')
        if auto_add:
            self.git('add', filename)

    def getFileContents(self, filename):
        with open(os.path.join(self.dir, filename), 'r') as f:
            return f.read()



class UpdateSubmoduleBumpProtectorTest(unittest.TestCase):

    def setUp(self):
        self.work_root = tempfile.mkdtemp()

        # Set up (bare) upstream repo.
        self.upstream = GitRepo(self.get_work_dir('upstream.git'), options=['--bare'])

        # Create another "upstream" library, to be used as submodule.
        self.library = GitRepo(self.get_work_dir('library.git'))
        self.library.editFile('README.txt', 'This is a library')
        self.library.git('commit', '-m', 'First commit of the library')

        # Clone repo to working copy.
        self.wc = GitRepo(self.get_work_dir('wc.git'), clone_source=self.upstream)
        # Make initial commit
        self.wc.editFile('README.txt', 'Hello world!')
        self.wc.git('commit', '-m', 'initial commit')
        self.wc.git('push', 'origin', 'master')
        # Another commit for adding a submodule
        self.wc.git('submodule', 'add', self.library.dir, 'lib/library')
        self.wc.git('commit', '-m', 'Added library as submodule')
        self.wc.git('push', 'origin', 'master')

        # Install update hook in upstream repo (using copy2 to preserve executability)
        shutil.copy2(UPDATE_HOOK, os.path.join(self.upstream.dir, 'hooks', 'update'))

        # Make another working copy
        self.other_wc = GitRepo(self.get_work_dir('other-wc.git'), clone_source=self.upstream)

    def tearDown(self):
        shutil.rmtree(self.work_root)

    def get_work_dir(self, *args):
        return os.path.join(self.work_root, *args)

    def testRegularPush(self):
        self.wc.editFile('README.txt', 'just some text')
        self.wc.git('commit', '-m', 'added more content')
        # Check before push
        data = self.other_wc.getFileContents('README.txt')
        self.assertEqual('Hello world!\n', data)
        # Push
        self.wc.git('push', 'origin', 'master')
        # Check after push
        self.other_wc.git('pull')
        data = self.other_wc.getFileContents('README.txt')
        self.assertEqual('Hello world!\njust some text\n', data)

    def testAddSubmodule(self):
        self.wc.git('submodule', 'add', self.library.dir, 'lib/other-library')
        self.wc.git('commit', '-m', 'Added library as another submodule')
        self.wc.git('push', 'origin', 'master')
        # Check in other wc
        self.other_wc.git('pull')
        self.other_wc.git('submodule', 'update', '--init')
        data = self.other_wc.getFileContents('lib/other-library/README.txt')
        self.assertEqual('This is a library\n', data)

    def testIntendedSubmoduleBump(self):
        # Update library
        self.library.editFile('README.txt', 'This version is a lot better')
        self.library.git('commit', '-m', 'Improved library')
        # Update submodule in worling copy.
        submodule = GitRepo(os.path.join(self.wc.dir, 'lib/library'))
        submodule.git('fetch')
        submodule.git('checkout', 'origin/master')
        self.wc.git('add', 'lib/library')
        self.wc.git('commit', '-m', 'Bumped submodule to latest version')
        self.wc.git('push', 'origin', 'master')
        # Check in other wc
        self.other_wc.git('pull')
        self.other_wc.git('submodule', 'update', '--init')
        data = self.other_wc.getFileContents('lib/library/README.txt')
        self.assertEqual('This is a library\nThis version is a lot better\n', data)

    def testUnintendedSubmoduleBump(self):
        # Update library
        self.library.editFile('README.txt', 'This version is a lot better')
        self.library.git('commit', '-m', 'Improved library')
        # Update submodule in working copy.
        submodule = GitRepo(os.path.join(self.wc.dir, 'lib/library'))
        submodule.git('fetch')
        submodule.git('checkout', 'origin/master')
        # Edit a file, make a commit, dragging the submodule bump along.
        self.wc.editFile('README.txt', 'Remember to clean the cat box.')
        self.wc.git('commit', '-a', '-m', 'Added todo to readme file')
        # The push should fail
        with self.assertRaises(ExecuteException) as context:
            self.wc.git('push', 'origin', 'master')
        exception = context.exception
        self.assertRegexpMatches(exception.stderr, 'Commit .* by .* touches a submodule, but does not mention it in the commit message')


    def testNewBranchPush(self):
        self.wc.git('checkout', '-b', 'feature-foo')
        self.wc.editFile('README.txt', 'starting to work on feature Foo')
        self.wc.git('commit', '-m', 'first commit on feature-foo')
        exit_code, stdout, stderr = self.wc.git('push', 'origin', 'feature-foo')
        self.assertRegexpMatches(stderr, r'\[new branch\]\s+feature-foo')
        # Check after push
        exit_code, stdout, stderr = self.other_wc.git('fetch')
        self.assertRegexpMatches(stderr, r'\[new branch\]\s+feature-foo')
        self.other_wc.git('checkout', 'origin/feature-foo')
        data = self.other_wc.getFileContents('README.txt')
        self.assertEqual('Hello world!\nstarting to work on feature Foo\n', data)


    def testDeleteBranchPush(self):
        #First, create a branch
        self.wc.git('checkout', '-b', 'feature-bar')
        self.wc.editFile('README.txt', 'This is experimental feature Bar')
        self.wc.git('commit', '-m', 'implemented feature-bar')
        exit_code, stdout, stderr = self.wc.git('push', 'origin', 'feature-bar')
        self.assertRegexpMatches(stderr, r'\[new branch\]\s+feature-bar')
        # Check in other wc
        exit_code, stdout, stderr = self.other_wc.git('fetch')
        self.assertRegexpMatches(stderr, r'\[new branch\]\s+feature-bar')
        # Delete branch again
        exit_code, stdout, stderr = self.wc.git('push', 'origin', ':feature-bar')
        self.assertRegexpMatches(stderr, r'\[deleted\].*feature-bar')
        # Check in other wc
        exit_code, stdout, stderr = self.other_wc.git('fetch', '--prune')
        self.assertRegexpMatches(stderr, r'\[deleted\]\s+\(none\)\s+->\s+origin/feature-bar')



# TODO: test pushing a merge without conflict
# TODO: test pushing a merge with conflict (dragging submodule bump along)


if __name__ == '__main__':
    unittest.main()
