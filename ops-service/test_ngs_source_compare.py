import unittest

from ngs_source_compare import NgsSourceCompare

A,B,C,D,E,F=(i*40 for i in 'abcdef')


class NgsSourceComparison(unittest.TestCase):
    def setUp(self):
        self.clock=0
        self.calls=[]
        self.mapping={
            'commits/'+A:{'sha':A,'tree':{'sha':C}},
            'commits/'+B:{'sha':B,'tree':{'sha':D}},
            'trees/'+C:{'sha':C,'truncated':False,'tree':[{'path':'ngs-local','type':'tree','sha':E}]},
            'trees/'+D:{'sha':D,'truncated':False,'tree':[{'path':'ngs-local','type':'tree','sha':E}]},
        }
        def fetch(path):
            assert path.startswith('/repos/pvenus/ngs-platform/git/')
            key=path.split('/git/')[1]
            self.calls.append(key)
            return self.mapping[key]
        self.comparison=NgsSourceCompare(fetch,lambda:self.clock)

    def test_same_shared_tree_different_repo_sha_and_cache(self):
        self.assertEqual(self.comparison.classify(A,B),'same-local-source')
        self.assertEqual(self.comparison.classify(B,A),'same-local-source')
        self.assertEqual(len(self.calls),4)

    def test_different_shared_trees(self):
        self.mapping['trees/'+D]['tree'][0]['sha']=F
        self.assertEqual(self.comparison.classify(A,B),'different-local-source')

    def test_unverified_never_issues_warning_and_can_recover(self):
        self.mapping['trees/'+D]['truncated']=True
        self.assertEqual(self.comparison.classify(A,B),'unknown')
        self.mapping['trees/'+D]['truncated']=False
        self.clock=31
        self.assertEqual(self.comparison.classify(A,B),'same-local-source')

    def test_exact_same_commit_no_lookup_and_invalid_ids_rejected(self):
        self.assertEqual(self.comparison.classify(A,A),'same-commit')
        self.assertEqual(self.comparison.classify(A,A+'/../'),'unknown')
        self.assertEqual(self.calls,[])
