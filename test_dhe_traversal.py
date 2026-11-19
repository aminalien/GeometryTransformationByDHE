"""Traversal ordering is observable in Inspect DHE and existing exports."""
import unittest
from shapely.geometry import box,Point
from shapely.affinity import translate
import DHECore as dhe
from test_mapping import build


def legacy_walk(edge):
    start=edge;seen={edge:None};faces=[edge];finished=False
    while not finished:
        edge=edge.NF
        if edge is start:
            index=-1
            while True:
                edge=list(seen)[index].S
                if edge not in seen:
                    faces.append(edge);break
                index-=1
                if abs(index)>len(seen):finished=True;break
            start=edge
        seen[edge]=None
    return list(seen),faces


class CellTraversalTests(unittest.TestCase):
    def test_edges_and_face_heads_match_legacy_in_primal_dual_and_exterior(self):
        dense=Point(0,0).buffer(3,quad_segs=12)
        for sources,target,mode in (([box(0,0,2,2),box(4,0,6,2)],box(0,0,6,2),'smooth'),
                                    ([dense],translate(dense,xoff=.5),'smooth')):
            result,_=build(sources,[target],mode=mode)
            for cell in result.model.all_cells:
                for seed in (cell.seed,cell.seed.D,cell.seed.Adjacent()):
                    expected_edges,expected_faces=legacy_walk(seed)
                    self.assertEqual(dhe.Cell(seed),expected_edges)
                    self.assertEqual(dhe.get_UniqueFace_in_cell(seed),expected_faces)
                    self.assertEqual({e for face in expected_faces for e in dhe.face_edges(face)},set(expected_edges))


if __name__=='__main__':unittest.main()
