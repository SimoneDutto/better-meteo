"""Git publication tested against temporary local repositories; never GitHub."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('publish_pages',Path(__file__).resolve().parents[1]/'scripts/publish_pages.py')
pages=importlib.util.module_from_spec(spec)
spec.loader.exec_module(pages)


class PublicationTests(unittest.TestCase):
    def test_project_user_and_ssh_repository_base_paths(self):
        self.assertEqual(pages.infer_base('git@github.com:SimoneDutto/better-meteo.git'),'/better-meteo/')
        self.assertEqual(pages.infer_base('https://github.com/name/weather'),'/weather/')
        self.assertEqual(pages.infer_base('ssh://git@github.com/name/name.github.io.git'),'/')
        with self.assertRaises(ValueError):pages.infer_base('/tmp/local-repository')
        self.assertEqual(pages.validate_base('/weather'),'/weather/')
        with self.assertRaises(ValueError):pages.validate_base('/../private/')

    def test_publication_replaces_only_generated_branch_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as temporary,patch.dict(os.environ,{'GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_NOSYSTEM':'1'}):
            root=Path(temporary)
            remote=root/'remote.git'
            pages.git('init','--bare','--quiet',str(remote))
            site=root/'export';site.mkdir()
            (site/'.better-meteo-export').write_text('generated')
            (site/'index.html').write_text('first weather snapshot')
            first=root/'first'
            pages.prepare_checkout(str(remote),first,'Offline test','test@example.invalid')
            pages.copy_site(site,first)
            self.assertTrue(pages.commit_and_push(first))
            original=pages.git('rev-parse','HEAD',cwd=first).stdout.strip()
            self.assertFalse(pages.commit_and_push(first))
            second=root/'second'
            pages.prepare_checkout(str(remote),second,'Offline test','test@example.invalid')
            (second/'obsolete.png').write_bytes(b'old')
            (second/'CNAME').write_text('weather.example.invalid\n')
            (site/'index.html').write_text('second weather snapshot')
            pages.copy_site(site,second)
            self.assertFalse((second/'obsolete.png').exists())
            self.assertEqual((second/'CNAME').read_text(),'weather.example.invalid\n')
            self.assertTrue((second/'.git').is_dir())
            self.assertTrue(pages.commit_and_push(second))
            self.assertEqual(pages.git('rev-parse','HEAD^',cwd=second).stdout.strip(),original)
            branches=pages.git('for-each-ref','--format=%(refname)',cwd=remote).stdout.splitlines()
            self.assertEqual(branches,['refs/heads/gh-pages'])

    def test_unrecognised_export_leaves_checkout_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);site=root/'export';site.mkdir();checkout=root/'checkout';checkout.mkdir()
            (checkout/'existing').write_text('retain')
            with self.assertRaises(ValueError):pages.copy_site(site,checkout)
            self.assertEqual((checkout/'existing').read_text(),'retain')

    def test_publish_preflight_failure_never_calls_collector(self):
        with patch.object(pages,'resolve_remote',side_effect=ValueError('missing remote')),patch.object(pages,'run') as run,patch('sys.argv',['publish_pages','publish']):
            with self.assertRaisesRegex(ValueError,'missing remote'):pages.main()
            run.assert_not_called()


if __name__=='__main__':unittest.main()
