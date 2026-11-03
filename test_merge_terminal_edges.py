"""Regression for fallback diagonals contacting the interior of terminal caps."""
from collections import Counter
from pathlib import Path
import unittest
from unittest.mock import patch

import geopandas as gpd
from shapely.geometry import Polygon,box
from shapely.ops import unary_union

from generalization_geometry import DEFAULT_DATA,load_shapefile,prepare_frame
from generalization_model import GeneralizationSession,_core,_polygon_patches
from plane_slicing import PlaneSlicer,SlicePlane
from shell_conformity import TransitionShellError
from top_face_overlay import cell_volume


def portable_data():
    donor=Polygon([(2,0),(8,0),(8,1),(6,1),(6,3),(4,3),(4,2),(2,2)])
    return prepare_frame(gpd.GeoDataFrame(geometry=[box(0,0,2,2),donor,box(20,0,22,2)],crs=2180))


def boundary_edges(patches):
    counts=Counter(tuple(sorted((a,b))) for p in patches for a,b in zip(p.points,p.points[1:]+p.points[:1]))
    return {edge for edge,count in counts.items() if count==1}


class TerminalEdgeTests(unittest.TestCase):
    def test_refinement_preserves_projected_domain_boundary_and_existing_vertices(self):
        s=GeneralizationSession(portable_data());source=s.state.features[1].geometry
        target=unary_union([source,s.state.features[2].geometry])
        for source,target in ((source,target),(target,source)):
            with self.subTest(growing=source.area<target.area):
                with patch('generalization_model._refine_terminal_internal_edges',side_effect=lambda ts,*args:ts):
                    previous,extended=_polygon_patches(1,source,target,5,15)
                actual,count=_polygon_patches(1,source,target,5,15)
                self.assertEqual(count,extended);self.assertGreater(extended,0)
                self.assertGreater(len(actual),len(previous))
                self.assertEqual(boundary_edges(actual),boundary_edges(previous))
                before={p for face in previous for p in face.points};after={p for face in actual for p in face.points}
                self.assertTrue(before<=after)
                self.assertTrue(all(5<p[2]<15 for p in after-before))
                domain=unary_union([p.geometry for p in actual])
                self.assertLess(domain.symmetric_difference(source.symmetric_difference(target)).area,1e-10)
                self.assertAlmostEqual(sum(p.geometry.area for p in actual),domain.area,places=10)
                self.assertTrue(all(max(p[2] for p in f.points)>min(p[2] for p in f.points) for f in actual))
                counts=Counter(tuple(sorted((a,b))) for f in actual for a,b in zip(f.points,f.points[1:]+f.points[:1]))
                self.assertFalse(any(n==2 and a[2]==b[2] and a[2] in (5,15) for (a,b),n in counts.items()))

    def test_portable_merge_and_unchanged_history(self):
        s=GeneralizationSession(portable_data());verify_merge(self,s,(1,2),1,10)

    def test_original_invalid_contact_still_rejected_without_refinement(self):
        s=GeneralizationSession(portable_data());state=s.state;core=_core()
        with patch('generalization_model._refine_terminal_internal_edges',side_effect=lambda ts,*args:ts):
            with self.assertRaises(TransitionShellError) as caught:s.preview('merging',[1,2],winner=1)
        self.assertEqual(caught.exception.report['classification'],'self_intersection')
        self.assertEqual(caught.exception.report['parts'],['transition','top'])
        self.assertIs(s.state,state);self.assertEqual(_core(),core)

    def test_failed_allocation_and_postvalidation_preserve_live_state(self):
        s=GeneralizationSession(portable_data());state=s.state;core=_core();history=list(s.history)
        for target in ('DHECore.MEF','generalization_model.GeneralizationSession._validate_sections'):
            with self.subTest(stage=target),patch(target,side_effect=RuntimeError('refined merge rollback')):
                with self.assertRaisesRegex(RuntimeError,'refined merge rollback'):s.preview('merging',[1,2],winner=1)
            self.assertIs(s.state,state);self.assertEqual(s.history,history);self.assertEqual(_core(),core)


def verify_merge(test,s,selection,winner,dz):
    old=s.state;core=_core();history=list(s.history)
    target=unary_union([old.features[k].geometry for k in selection])
    proposal=s.preview('merging',selection,winner=winner,dz=dz)
    test.assertIs(s.state,old);test.assertEqual(s.history,history);test.assertEqual(_core(),core)
    slicer=PlaneSlicer(proposal.model);last=old.features[winner].geometry
    losers=set(selection)-{winner};last_donors={str(k):old.features[k].geometry for k in losers}
    for fraction in (0.,.1,.25,.5,.75,.9,.999,1.):
        sections=slicer.sections(SlicePlane.horizontal(old.z+fraction*dz))
        current=sections[str(winner)]
        test.assertLess(last.difference(current).area,1e-7)
        test.assertLess(current.difference(target).area,1e-7)
        combined=unary_union([g for k,g in sections.items() if int(k) in selection])
        test.assertLess(combined.symmetric_difference(target).area,1e-7)
        for loser in losers:
            donor=sections.get(str(loser),Polygon())
            test.assertLess(donor.difference(last_donors[str(loser)]).area,1e-7)
            test.assertLess(donor.intersection(current).area,1e-7)
            last_donors[str(loser)]=donor
        if fraction==1:test.assertLess(current.symmetric_difference(target).area,1e-7)
        last=current
    test.assertTrue(all(cell_volume(c)>0 for c in proposal.model.cells))
    for k,g in slicer.sections(SlicePlane.horizontal(old.z/2)).items():
        test.assertLess(g.symmetric_difference(old.features[int(k)].geometry).area,1e-7)
    test.assertEqual(set(proposal.state.operation.retired),losers)
    test.assertTrue(losers.isdisjoint(proposal.state.features))
    test.assertEqual(proposal.state.operation.winner,winner)
    # Caps remain polygon faces; only transition facets acquire new vertices.
    caps=[f for f in proposal.state.faces if f.owner==winner and f.part=='top']
    test.assertEqual(len(caps),1)
    return proposal


@unittest.skipUnless(Path(DEFAULT_DATA).is_file(),'Local default Shapefile unavailable')
class ReportedMergeTests(unittest.TestCase):
    def test_reported_merge_both_winners_levels_and_increments(self):
        data=load_shapefile()
        for source_z in (5.,15.,25.):
            s=GeneralizationSession(data,initial_height=source_z)
            for winner in (14,12):
                for dz in (1.,10.,20.):
                    with self.subTest(source_z=source_z,winner=winner,dz=dz):
                        verify_merge(self,s,(12,14),winner,dz)


if __name__=='__main__':unittest.main()
