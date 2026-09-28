import copy
import random
import unittest

from export_august import read_json
from final_sa import construct
from final_validator import validate
from sa_feasible_recommendations import CONFIG, FeasibleNeighborhood, canonical, distance, groups, search, select
from test_final_sa_engine import small_model
from test_final_foundation import model, event


class FeasibleSATests(unittest.TestCase):
    def setUp(self):
        self.model=small_model()
        self.initial=construct(self.model,random.Random(8))[0]
        self.config=read_json(CONFIG)
        self.config.update(max_evaluations=450,stagnation_limit=200)

    def test_proposals_and_incremental_occupancy_match_independent_validator(self):
        m=self.model;n=FeasibleNeighborhood(m);x=self.initial.copy();busy=n.occupancy(x);rng=random.Random(42)
        operations=set()
        for _ in range(100):
            y,meta=n.propose(x,busy,rng)
            self.assertTrue(validate(m,m.timetable(y))['feasible'])
            self.assertLessEqual(meta['attempts'],50)
            if meta['operator']!='no_change':self.assertNotEqual(y,x)
            operations.add(meta['operator']);n.apply(busy,x,meta['changes']);x=y
            self.assertEqual(busy,n.occupancy(x))
        self.assertIn('move',operations);self.assertIn('swap',operations)

    def test_seed_reproducibility_no_mutation_and_budget(self):
        initial=copy.deepcopy(self.initial)
        a=search(self.model,self.initial,self.config,2000)
        b=search(self.model,self.initial,self.config,2000)
        a.pop('runtime_seconds');b.pop('runtime_seconds')
        self.assertEqual(a,b);self.assertEqual(initial,self.initial)
        self.assertLessEqual(a['evaluations'],450)
        self.assertTrue(a['best_feasible_quality']['feasible'])
        self.assertLessEqual(a['best_feasible_quality']['S'],a['initial_quality']['S'])
        saved=copy.deepcopy(a['best_feasible']);a['final_current'].clear()
        self.assertEqual(a['best_feasible'],saved)

    def test_identical_event_labels_do_not_create_fake_diversity(self):
        m=model([event('a'),event('b')]);g=groups(m)
        a={'a':('d1',1),'b':('d2',1)};b={'a':('d2',1),'b':('d1',1)}
        self.assertEqual(canonical(g,a),canonical(g,b));self.assertEqual(distance(g,a,b),0)
        c={'a':('d1',2),'b':('d2',1)}
        self.assertEqual(distance(g,a,c),1)

    def test_screen_rejects_infeasible_initial(self):
        m=model([event('a'),event('b')]);x={'a':('d1',1),'b':('d1',1)}
        with self.assertRaises(ValueError):search(m,x,self.config,2000)

    def test_initial_copy_cannot_be_selected_as_sa_recommendation(self):
        quality=self.model.evaluate(self.initial)
        run={'seed':2000,'dataset_hash':self.model.dataset_hash,'rules_hash':self.model.rules_hash,
             'best_feasible':self.initial,'best_feasible_quality':quality}
        self.assertEqual(select(self.model,[run],self.initial,self.config)['recommendations'],[])


if __name__=='__main__':unittest.main()
