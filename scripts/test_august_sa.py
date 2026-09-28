import copy
import random
import unittest
from anneal_august import Problem, load_problem, anneal, metropolis, repair, SCORE_KEYS
from model_august import domain, evaluate

class SATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.full,_=load_problem()

    def toy(self):
        es=[{'id':str(i),'teacher_codes':['1'],'class_ids':[f'X-{i}'],'subject_id':'MAT',
             'time':{'jp':1,'kbm_minutes':45,'breaks':[]}} for i in [1,2,3]]
        ds={e['id']:domain(e,self.full.calendar) for e in es}
        initial={e['id']:{'day':'selasa','start_period':p} for e,p in zip(es,[1,3,5])}
        return Problem(es,self.full.calendar,ds,initial,1000)

    def config(self):return dict(seed=7,iterations=200,t0=50,tmin=.1,alpha=.9,chain_length=20,swap_probability=.7,repair_rounds=0)

    def test_equivalence_on_random_complete_states(self):
        p=self.full;rng=random.Random(917)
        for state in [p.initial]+[[rng.choice(options) for options in p.options] for _ in range(25)]:
            quick=p.score(state)
            reference=evaluate(p.events,p.calendar,p.domains,p.solution(state),p.M)
            self.assertEqual(quick,{k:reference[k] for k in SCORE_KEYS})

    def test_neighbor_preserves_domains_ids_and_event_registry(self):
        p=self.full;rng=random.Random(11);before=copy.deepcopy(p.events);state=p.initial.copy()
        for _ in range(500):
            candidate,_=p.propose(state,rng,.7)
            if candidate is not None:
                self.assertEqual(len(candidate),len(p.events))
                self.assertTrue(all(pos in p.allowed[i] for i,pos in enumerate(candidate)))
                state=candidate
        self.assertEqual(p.events,before)

    def test_seed_repeats_trajectory(self):
        p=self.toy();a=anneal(p,self.config());b=anneal(p,self.config())
        for value in [a,b]:value.pop('search_seconds')
        self.assertEqual(a,b)

    def test_best_retained_even_if_current_worsens(self):
        p=self.toy();config=self.config();config['t0']=10000
        r=anneal(p,config)
        self.assertLessEqual(r['best_score']['F'],r['final_score']['F'])
        self.assertLessEqual(r['best_score']['F'],r['sa_initial_score']['F'])
        self.assertIsNotNone(r['best_feasible_state'])
        self.assertEqual(p.score(r['best_feasible_state'])['H'],0)

    def test_metropolis_probability(self):
        class Fixed:
            def __init__(self,value):self.value=value
            def random(self):return self.value
        self.assertTrue(metropolis(0,1,Fixed(1)))
        self.assertTrue(metropolis(-100,1,Fixed(1)))
        self.assertTrue(metropolis(1,1,Fixed(.3)))
        self.assertFalse(metropolis(1,1,Fixed(.4)))
        self.assertFalse(metropolis(1e9,.1,Fixed(.1)))

    def test_budget_and_temperature_stop(self):
        config=self.config();config.update(iterations=100,chain_length=1,t0=2,tmin=1,alpha=.4)
        r=anneal(self.toy(),config)
        self.assertLessEqual(r['iterations'],1)
        self.assertIn(r['stop_reason'],['temperature_minimum','zero_lower_bound'])

    def test_repair_report_and_reference_unchanged(self):
        p=self.full;original=p.initial.copy()
        state,report=repair(p,p.initial,10)
        self.assertEqual(p.initial,original)
        self.assertEqual(p.score(state),report['score'])
        self.assertEqual(report['score']['H'],0)
        self.assertGreater(report['evaluations'],0)

    def test_reject_bad_parameters(self):
        for field,value in [('t0',0),('alpha',1),('iterations',0),('swap_probability',2),('repair_rounds',-1)]:
            config=self.config();config[field]=value
            with self.assertRaises(ValueError):anneal(self.toy(),config)

if __name__=='__main__':unittest.main()
