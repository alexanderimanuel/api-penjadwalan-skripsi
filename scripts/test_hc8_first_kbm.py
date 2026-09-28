"""HC8 first-KBM clarification, independent validator and neighborhood regression."""
import random
import unittest
from final_rules import FinalModel
from test_final_foundation import event, fixture
from sa_feasible_recommendations import FeasibleNeighborhood

class FirstTeachingSlotTests(unittest.TestCase):
    def make(self, events):
        data,rules=fixture(events)
        rules['class_start_first_kbm']=True
        return FinalModel(data,rules)

    def check(self,m,x,expected):
        a,b=m.score(x),m.evaluate(x)
        self.assertEqual(a['HC']['HC8'],expected)
        self.assertEqual(a['HC'],b['HC'])
        self.assertEqual(a['raw'],b['raw'])

    def test_single_late_event_counts_leading_slots(self):
        m=self.make([event('a')])
        self.check(m,{'a':('d1',3)},2)
        self.check(m,{'a':('d1',1)},0)

    def test_leading_and_internal_holes(self):
        m=self.make([event('a'),event('b')])
        self.check(m,{'a':('d1',2),'b':('d1',4)},2)
        self.assertEqual(m.evaluate({'a':('d1',2),'b':('d1',4)})['details']['HC8'][0]['empty_kbm_periods'],[1,3])

    def test_break_fixed_and_early_finish(self):
        m=self.make([event(str(i)) for i in range(5)])
        self.check(m,{str(i):('d1',p) for i,p in enumerate([1,2,3,4,6])},0)

    def test_neighbor_preserves_first_slot(self):
        m=self.make([event('a'),event('b')])
        x={'a':('d1',1),'b':('d1',2)}
        n=FeasibleNeighborhood(m);occupied=n.occupancy(x);rng=random.Random(73)
        for _ in range(100):
            y,meta=n.propose(x,occupied,rng)
            self.assertTrue(m.evaluate(y)['feasible'])
            n.apply(occupied,x,meta['changes']);x=y

if __name__=='__main__':unittest.main()
