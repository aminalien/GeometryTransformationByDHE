"""Local angular allowances preserve event data and reject unrelated defects."""
from dataclasses import replace
from math import cos,sin,radians
import unittest

from skeleton_timing import TimingNode,TimingFace,TimingArc,TimingGraph,TimingEnvelope
from shell_conformity import TransitionShellError


def graph(angle=.2):
    a=radians(angle)
    return TimingGraph(0,
        (TimingNode((0.,0.),0.),TimingNode((0.,2.),2.),
         TimingNode((1.,3.),3.),TimingNode((-1.,3.),3.),TimingNode((0.,4.),4.)),
        (TimingFace(((-1.,0.),(0.,0.)),(0,1),'a'),
         TimingFace(((0.,0.),(cos(a),sin(a))),(0,1),'b')),
        (TimingArc(0,1,(0,1)),TimingArc(1,2,()),TimingArc(1,3,()),
         TimingArc(2,3,()),TimingArc(2,4,()),TimingArc(3,4,())),.26)


class TimingEnvelopeTests(unittest.TestCase):
    def test_verified_seed_interpolation_and_unique_branch_propagation(self):
        g=graph();saved=repr(g);envelope=TimingEnvelope((g,))
        self.assertEqual(len(envelope.seeds),1)
        delta=2*(1-cos(radians(.2)))
        for fraction in (0.,.5,1.):
            time,weights=envelope.sample((0,0,1),fraction)
            self.assertEqual(time,2*fraction)
            self.assertAlmostEqual(sum(weights.values()),delta*fraction,places=14)
        # The equal-time pair and the diamond share one seed, never two copies.
        for node in (2,3,4):
            self.assertEqual(len(envelope.weights[0][node]),1)
            self.assertAlmostEqual(sum(envelope.weights[0][node].values()),delta,places=14)
        self.assertEqual(repr(g),saved)

    def test_reversed_arc_preserves_original_sampling_order(self):
        g=graph();g=replace(g,arcs=(TimingArc(1,0,(0,1)),))
        envelope=TimingEnvelope((g,))
        time,weights=envelope.sample((0,1,0),.25)
        self.assertEqual(time,1.5)
        self.assertAlmostEqual(sum(weights.values()),1.5*(1-cos(radians(.2))),places=14)

    def test_unverified_rays_receive_no_allowance(self):
        g=graph()
        for candidate in (graph(2),replace(g,nodes=(g.nodes[0],TimingNode((.01,2.),2.),*g.nodes[2:])),
                          replace(g,faces=(replace(g.faces[0],edge=((-2.,0.),(-1.,0.))),g.faces[1]))):
            with self.subTest(graph=candidate):self.assertFalse(TimingEnvelope((candidate,)).seeds)

    def test_invalid_provenance_and_fractions_rejected(self):
        e=TimingEnvelope((graph(),))
        for provenance,fraction in (((0,9,1),.5),((0,0,1),-.01),((0,0,1),float('nan'))):
            with self.assertRaises(TransitionShellError):e.sample(provenance,fraction)


if __name__=='__main__':unittest.main()
