"""DHE construction primitives extracted from aminalien/DHE.

Source: https://github.com/aminalien/DHE/blob/main/DHECore.py
Source blob: a4a7a56e7881611c39b50973c40e571dab84a011
Keeps construction, sewing, traversal, and required support operations.
"""

from math import inf, sqrt
import numpy as np

dhe = []
killdhe = set()
killVertexes = []


def face_edges(seed):
    """Return a face cycle, rejecting open or incorrectly closed boundaries."""
    edges = []
    seen = set()
    edge = seed
    while edge not in seen:
        if edge is None:
            raise ValueError("An open face was found in the DHE topology.")
        seen.add(edge)
        edges.append(edge)
        edge = edge.NF
    if edge is not seed:
        raise ValueError("A face cycle does not return to its starting edge.")
    return edges


class Vertex:
    cntId = 1

    def __init__(self, x=0.0, y=0.0, z=0.0, ch=-1, clr=(0, 0, 1,1)):
        self.x = x
        self.y = y
        self.z = z

        self.color = clr
        if ch == -1:
            self.i = Vertex.cntId
            Vertex.cntId += 1
        else:
            self.i = ch
        self.FaceList=list()
        self.info=None
        self.Important = None


class DualHalfEdge:
    cntId = 1

    def __init__(self, ch, chh=0):
        self.NV = self
        self.NF = self
        self.S = self
        self.V = None
        self.D = None
        self.prev = self.S.NV
        self.slicing = None
        self.exterainfo=None

        if (ch == 'P' and (chh == 0 or chh == 'INT')):
            self.i = 10 * (DualHalfEdge.cntId) + 0
            self.info = "e" + str(DualHalfEdge.cntId)
        if (ch == 'D' and (chh == 0 or chh == 'INT')):
            self.i = 10 * (DualHalfEdge.cntId) + 1
            self.info = "e" + str(DualHalfEdge.cntId) + ".D"
        if (ch == 'P' and chh == 'Sym'):
            self.i = 10 * (DualHalfEdge.cntId) + 2
            self.info = "e" + str(DualHalfEdge.cntId) + ".S"
        if (ch == 'D' and chh == 'Sym'):
            self.i = 10 * (DualHalfEdge.cntId) + 3
            self.info = "e" + str(DualHalfEdge.cntId) + ".S.D"
            DualHalfEdge.cntId += 1
        if (ch == 'P' and chh == 'AdjSymINT'):
            self.i = 10 * (DualHalfEdge.cntId) + 2
            self.info = "e" + str(DualHalfEdge.cntId) + ".S"
        if (ch == 'D' and chh == 'AdjSymINT'):
            self.i = 10 * (DualHalfEdge.cntId) + 3
            self.info = "e" + str(DualHalfEdge.cntId) + ".S.D"
        if (ch == 'P' and chh == 'Adj'):
            self.i = 10 * (DualHalfEdge.cntId) + 4
            self.info = "e" + str(DualHalfEdge.cntId) + ".Adj"
        if (ch == 'D' and chh == 'Adj'):
            self.i = 10 * (DualHalfEdge.cntId) + 5
            self.info = "e" + str(DualHalfEdge.cntId) + ".Adj.D"
        if (ch == 'P' and chh == 'AdjSym'):
            self.i = 10 * (DualHalfEdge.cntId) + 6
            self.info = "e" + str(DualHalfEdge.cntId) + ".Adj.S"
        if (ch == 'D' and chh == 'AdjSym'):
            self.i = 10 * (DualHalfEdge.cntId) + 7
            self.info = "e" + str(DualHalfEdge.cntId) + ".Adj.S.D"
            DualHalfEdge.cntId += 1
    def __repr__(self):
            return f"DHE (Info: {self.info})"
    def PrevV(self):
        return self.D.NV.D

    def Adjacent(self):
        return self.D.NF.D.S

    def NextE(self):
        return self.Adjacent().S

    def PrevF(self):
        return self.Adjacent().NF.Adjacent()

# -------------------- Constructors --------------------


def MakeHalfEdge(P, D, chh=0):
    eP = DualHalfEdge('P', chh)
    eD = DualHalfEdge('D', chh)
    eP.NV = eP
    eP.NF = eP
    eP.S = eP
    eP.V = P
    eD.V = D
    eD.NV = eD
    eD.NF = eD
    eD.S = eD
    eP.D = eD
    eD.D = eP
    return eP


def MakeEdge(P1, P2, D, chh=0):
    e = MakeHalfEdge(P1, D, chh)
    if chh == 0:
        eSym = MakeHalfEdge(P2, D, 'Sym')
    if chh == 'Adj':
        eSym = MakeHalfEdge(P2, D, 'AdjSym')
    if chh == 'INT':
        eSym = MakeHalfEdge(P2, D, 'AdjSymINT')
    ConnectHalfEdges(e, eSym)
    e.NF = eSym
    eSym.NF = e
    ConnectHalfEdges(e.D, eSym.D)
    e.D.NF = eSym.D
    eSym.D.NF = e.D
    return e


def KillEdge(e):
    KillHalfEdge(e.S)
    KillHalfEdge(e)


def ConnectHalfEdges(e1, e2):
    e1.S = e2
    e2.S = e1


def Snap(e1, e2):
    eTemp = e1.S.NF
    e1.S.NF = e2.S.NF
    e2.S.NF = eTemp
    eTemp = e1.NF
    e1.NF = e2.NF
    e2.NF = eTemp
    eTemp = e1.S
    ConnectHalfEdges(e1, e2.S)
    ConnectHalfEdges(e2, eTemp)


def HalfSplice(e1, e2, NF_Change=True):
    e1Next = e1.NV
    e2Next = e2.NV
    e2.NV = e1Next
    e1.NV = e2Next
    if NF_Change:
        e1Next.S.NF = e2
        e2Next.S.NF = e1


def KillHalfEdge(e):

    # Python GC will collect when references drop
    del e


def Splice(e1, e2, NF_Change=True):
    HalfSplice(e1.NV.D, e2.NV.D, NF_Change)
    HalfSplice(e1, e2, NF_Change)


def ComplexSplice(e1, e2):
    e1Temp = e1.D.S.D
    e2Temp = e2.D.S.D
    Splice(e1, e2)
    Splice(e1Temp, e2Temp)
    Snap(e1.D.S, e2Temp.D)
    Snap(e2.D.S, e1Temp.D)


def Sew(e1, e2Sym):
    e1Sym = e1.S
    e2 = e2Sym.S
    e1PrevV = e1.PrevV()
    e2PrevV = e2.PrevV()
    e1SymPrevV = e1Sym.PrevV()
    e2SymPrevV = e2Sym.PrevV()
    Snap(e1, e2Sym)
    Splice(e1PrevV, e2SymPrevV)
    Splice(e1SymPrevV, e2PrevV)


def MakeDegenerateEdge(P, D):
    e = MakeHalfEdge(P, D)
    eSym = MakeHalfEdge(P, D, 'Sym')
    ConnectHalfEdges(e, eSym)
    HalfSplice(e, eSym)
    ConnectHalfEdges(e.D, eSym.D)
    HalfSplice(e.D, eSym.D)
    return e


def MakeFacePList(pList, D):
    global dhe
    e1 = MakeDegenerateEdge(pList[0], D)
    dhe.append(e1)
    for P in range(1, len(pList)):
        e2 = MakeDegenerateEdge(pList[P], D)
        dhe.append(e2)
        Snap(e1.NV, e2)
    return e1


def almost_equal(a, b, atol=1e-3):

        return abs(float(a) - float(b)) <= atol


def MakeFace(coords,Z ,VI,VINF, exterainfoforHEs=None):
    ##coords are counterclockwise (CCW) and the first and last points are duplicated and return e.S in CCW
    p1 = Vertex(coords[0][0], coords[0][1], Z)
    p2 = Vertex(coords[1][0], coords[1][1], Z)
    e1 = MEVVFS(p1, p2, VI, VINF)
    e1.exterainfo=exterainfoforHEs
    e1.S.exterainfo = exterainfoforHEs
    dhe.append(e1)
    e=e1
    for i in range(2, len(coords)-1):

        p = Vertex(coords[i][0], coords[i][1], Z)

        # count += 1
        e=MEV(p, e.S)
        dhe.append(e)
        e.exterainfo = exterainfoforHEs
        e.S.exterainfo = exterainfoforHEs

    e=MEF(e1, e.S)
    dhe.append(e.S)
    e.exterainfo = exterainfoforHEs
    e.S.exterainfo = exterainfoforHEs
    return e.S


def MakeLine(Coords,Z, VI,VINF, exterainfoforHEs=None ):

    p1=Vertex(Coords[0][0],Coords[0][1],Z)
    p2 = Vertex(Coords[1][0], Coords[1][1], Z)
    erightLine=MEVVFS(p1,p2,VI,VINF)

    e=erightLine
    e.exterainfo = exterainfoforHEs
    e.S.exterainfo = exterainfoforHEs
    # erightLine=dhe[-1]
    for i in range(2,len(Coords)):
        l=Coords[i]
        p = Vertex(l[0], l[1], Z)
        e = MEV(p, e.S)
        e.exterainfo = exterainfoforHEs
        e.S.exterainfo = exterainfoforHEs

    eleftLine = e.S
    # print(erightLine.info,eleftLine.info)
    return  erightLine,eleftLine


def MakeLineZeroFace(Coords, Z, VI, VINF):
    Coords_inv = list(reversed(Coords))
    Coords_inv.pop(0)
    Coords.extend(Coords_inv)
    e=MakeFace(Coords,Z, VI, VINF)
    return e


def MakeDuplicateFace(e1,VI, VINF, Z=None):
    coords = list()

    starteT = e1
    while (True):
        coords.append((e1.V.x, e1.V.y, e1.V.z))
        e1 = e1.NF
        if starteT == e1:
            coords.append((e1.V.x, e1.V.y, e1.V.z))
            break
    if not Z:
        eNew = MakeInClinedFace(coords, VI, VINF)
    else:
        eNew = MakeFace(coords, Z,  VI, VINF)
    return eNew


def MakeDuplicateFaceSameVertices(e,VI, VINF):
    ##coords are counterclockwise (CCW) and the first and last points are duplicated and return e.S in CCW
    p1 = e.V
    p2 = e.S.V
    e1 = MEVVFS(p1, p2, VI, VINF)
    starte1=e1
    dhe.append(e1)
    starte=e.PrevF()
    while(True):
        e=e.NF
        p=e.S.V
        e1 = MEV(p, e1.S)
        dhe.append(e1)
        if starte==e:
            break



    ComplexSplice(starte1,e1.S)
    dhe.append(e1.S)

    return e1.S


def FindMatchesHE(e1,e2, TypeOfMatch='start', tol = 1e-3, OneMatch=False):
    # tol = 1e-1
    # print("Dakhle", e1, e2)
    # print("neeeeeeew" , e1.info , e2.info)
    ##TypeOfMatch = 'start' or 'equal' or 'sym' or 'juststart':
    # 'start'=  start point to be match, the end point not matter to be match or not  end ;
    # 'juststart': just start point should be match;
    # 'equal': start and end point to be matched
    # 'nearest':
    MatchesHEList=list()
    checkoneMatch=False
    starte1=e1
    starte2=e2
    MinDist=inf
    # print(OneMatch)
    while(True):


        while (True):

            if TypeOfMatch=='sym':
                # print("Dakhle" , e1.info , e2.info )

                if almost_equal(e1.V.x, e2.S.V.x, atol=tol) and almost_equal(e1.V.y, e2.S.V.y, atol=tol) and almost_equal(e1.S.V.x, e2.V.x, atol=tol) and almost_equal(e1.S.V.y, e2.V.y, atol=tol) :
                    if not almost_equal(e1.V.z, e1.S.V.z, atol=tol) or not almost_equal(e2.V.z, e2.S.V.z, atol=tol):
                        if almost_equal(e1.V.z, e2.S.V.z, atol=tol) and almost_equal(e1.S.V.z, e2.V.z, atol=tol):
                            # print(f'e1info : {e1.info}',[e1.V.x,e1.V.y,e1.V.z] , f'e2info : {e2.info}',[e2.V.x,e2.V.y,e2.V.z])
                            MatchesHEList.append([e1, e2])
                    else:
                        # print("ffff",e1.info , e2.info )
                        MatchesHEList.append([e1,e2])
                    if OneMatch:
                        checkoneMatch=True
                        break
            if TypeOfMatch=='equal':
                if almost_equal(e1.V.x, e2.V.x, atol=tol) and almost_equal(e1.V.y, e2.V.y, atol=tol) and almost_equal(e1.S.V.x, e2.S.V.x, atol=tol) and almost_equal(e1.S.V.y, e2.S.V.y, atol=tol):

                    MatchesHEList.append([e1,e2])
                    if OneMatch:
                        checkoneMatch=True
                        break
            if TypeOfMatch=='start':
                if almost_equal(e1.V.x, e2.V.x, atol=tol) and almost_equal(e1.V.y, e2.V.y, atol=tol) and not( almost_equal(e1.S.V.x, e2.S.V.x, atol=tol) and almost_equal(e1.S.V.y, e2.S.V.y, atol=tol)):
                    MatchesHEList.append([e1,e2])
                    if OneMatch:
                        checkoneMatch=True
                        break
            if TypeOfMatch == 'juststart':
                if almost_equal(e1.V.x, e2.V.x, atol=tol) and almost_equal(e1.V.y, e2.V.y, atol=tol):
                    MatchesHEList.append([e1, e2])
                    if OneMatch:
                        checkoneMatch = True
                        break
            if TypeOfMatch=='justsym':
                # print("Dakhle" , e1.info , e2.info )

                if almost_equal(e1.V.x, e2.S.V.x, atol=tol) and almost_equal(e1.V.y, e2.S.V.y, atol=tol) and almost_equal(e1.S.V.x, e2.V.x, atol=tol) and almost_equal(e1.S.V.y, e2.V.y, atol=tol) :

                     MatchesHEList.append([e1, e2])

                     if OneMatch:
                        checkoneMatch=True
                        break
            if TypeOfMatch == 'nearest':
                dist=sqrt(pow(e1.V.x-e2.V.x,2)+pow(e1.V.y-e2.V.y,2))
                if dist <MinDist:
                    MatchesHEList=[dist,e1,e2]
                    MinDist=dist

            e2 = e2.NF
            if starte2 == e2 or checkoneMatch:
                break
        e1=e1.NF
        if starte1==e1 or checkoneMatch:
            break
    return MatchesHEList


def CheckVertexinDheList(coord, atol=1e-4):
    for e in dhe:
        if almost_equal(coord[0], e.V.x, atol=atol) and almost_equal(coord[1], e.V.y, atol=atol) and almost_equal(coord[2], e.V.z, atol=atol):
            # print("FInd")
            return e.V
        if almost_equal(coord[0], e.S.V.x, atol=atol) and almost_equal(coord[1], e.S.V.y, atol=atol) and almost_equal(coord[2], e.S.V.z, atol=atol):
            # print("FInd")
            return e.S.V
    return None


def MakeInClinedFace(coords,VI,VINF):
    ##coords are counterclockwise (CCW) and the first and last points are duplicated and coords=[(x0,y0,z0) , (x1,y1,z1) ,....)
    v1=CheckVertexinDheList(coords[0])

    if v1:
        p1=v1
    else:
        p1 = Vertex(coords[0][0], coords[0][1], coords[0][2])

    v2 = CheckVertexinDheList(coords[1])
    if v2:
        p2=v2
    else:
        p2 = Vertex(coords[1][0], coords[1][1], coords[1][2])
    e1 = MEVVFS(p1, p2, VI, VINF)
    dhe.append(e1)
    dhe.append(e1.S)
    e=e1
    for i in range(2, len(coords)-1):

        p = Vertex(coords[i][0], coords[i][1], coords[i][2])

        # count += 1
        e=MEV(p, e.S)
        dhe.append(e)
        dhe.append(e.S)

    e=MEF(e1, e.S)
    dhe.append(e.S)
    dhe.append(e)
    return e.S


def KillFace(e):
    global killdhe
    # starte=e
    # while(True):
    #     # killdhe.add(e)
    #     eNF=e.NF
    #     KEF(e)
    #     e=eNF
    #     if starte==e:
    #         break

    starte = e
    e = e.NF
    if (len(killdhe) == 0):
        killdhe = {starte}
    else:
        killdhe.add(starte)
    while e != starte:
        beforee = e.NF
        KillEdge(e)
        killdhe.add(e)
        e = beforee
    KillEdge(starte)

# -------------------- Complex edge ops --------------------


def MakeComplexEdge(P1, P2, I, E):
    eINT = MakeEdge(P1, P2, I, 'INT')
    eEXT = MakeEdge(P1, P2, E, 'Adj')
    Snap(eINT.D.S, eEXT.D)
    dhe.append(eINT)
    dhe.append(eINT.S)
    # dhe.append(eEXT)
    return eINT


def KillComplexEdge(e):
    KillEdge(e.Adjacent())
    KillEdge(e)
    killdhe.add(e)
    killdhe.add(e.S)


def JoinByFace(e1, e2):
    faceToKill = e1.Adjacent();
    e1 = e1.Adjacent();
    e2 = e2.Adjacent();
    starte1=e1
    starte2 = e2;
    Listf1 = [e1];
    Listf2 = [e2];
    Listff1 = [e1.NF.D.S];
    Listff2 = [e2.D];
    while True:
        e1 = e1.NF;
        e2 = e2.PrevF();
        if e2 == starte2:
            break;
        else:
            Listf1.append(e1);
            Listf2.append(e2);
            Listff1.append(e1.NF.D.S);
            Listff2.append(e2.D);
    # for i in range(len(Listf2)):
    # print(Listf2[i].i);
    # Listff1=[e1.Adjacent(),e1.NF.Adjacent(), e1.NF.NF.Adjacent(), e1.NF.NF.NF.Adjacent()];
    for i in range(len(Listf1)):
        # print("inside", Listf1[i].info, Listf2[i].info)
        # print(Listf2[i].i)
        if Listf1[i] != Listf2[i].S:
            Sew(Listf1[i], Listf2[i].S);

    for i in range(len(Listf1)):
        if Listff1[i] != Listff2[i].S:
            Snap(Listff1[i], Listff2[i]);
    KillFace(faceToKill);
    # for i in range(4):
    # KillEdge(Listf1[i])
# def JoinByFaceUpdated(e1,e2):
#     faceToKill = e1.Adjacent()
#
#     starte1=e1
#     starte2=e2
#     e1List=[]
#     e2List=[]
#     while (True):
#         e1List.append(e1)
#         e1=e1.NF
#         if e1==starte1:
#             break
#     while (True):
#         e2List.append(e1)
#         e2=e2.PrevF()
#         if e2==starte2:
#             break
#     for i in range(len(e1List)):
#         e1=e1List[i]
#         e2=e2List[i]
#         if e1.Adjacent() != e2.Adjacent().S:
#             Sew(e1.Adjacent(), e2.Adjacent().S)
#     for i in range(len(e1List)):
#         e1=e1List[i]
#         e2=e2List[i]
#         Snap(e1.NF.Adjacent().D.S, e2.Adjacent().D)
#     # KillFace(faceToKill)


def MEF(e1, e2):
    e = MakeComplexEdge(e1.V, e2.V, e1.D.V, e1.Adjacent().D.V)
    ComplexSplice(e, e1)
    ComplexSplice(e2, e.S)
    return e


def KEF(e):
    global killdhe
    eSym = e.S
    eNextF = e.NF
    ComplexSplice(e, eSym.NF)
    ComplexSplice(eSym, eNextF)
    killdhe.add(e)
    # killdhe.add(e.S.Adjacent())

    KillComplexEdge(e)


def MEV(P, e):
    eN = MakeComplexEdge(e.V, P, e.D.V, e.Adjacent().D.V)
    # dhe.append(eN)
    # dhe.append(eN.S)
    ComplexSplice(eN, e)
    return eN


def MEVVFS(P1, P2, I, E):
    return MakeComplexEdge(P1, P2, I, E)


def MVE(P, e):
    starte = e
    newEdgesList = list()
    while True:
        newEdgesList.append(MakeDegenerateEdge(P, e.D.V))
        e = e.NextE()
        if e == starte:
            break

    i = 0
    while True:
        eN = newEdgesList[i]
        eNN = newEdgesList[(i + 1) % len(newEdgesList)]
        Snap(eN, e)
        Snap(eN.D.S, eNN.NV.D)
        i += 1
        e = e.NextE()
        if e == starte:
            break
    dhe.append(newEdgesList[0])
    return newEdgesList[0]


def MEFVertexes(vertexes, v1, v2):
    global dhe
    e1 = MEVVFS(vertexes[0], vertexes[1], v1, v2)
    lenDhe = len(dhe)
    if len(dhe) == 0:
        dhe = [e1]
    else:
        dhe.append(e1)
    for i in range(2, len(vertexes)):
        e = MEV(vertexes[i], dhe[len(dhe) - 1].S)
        dhe.append(e)
    e = MEF(dhe[len(dhe) - 1].S, dhe[lenDhe])
    dhe.append(e)


def MZEV(e1, e2, Vn=None):
    if not Vn:

        Vn=e1.V

    ComplexSplice(e1, e2);
    eN=MEF(e1, e2)
    dhe.append(eN)
    starte2=e2
    e2NVList=list()
    while(True):
        e2NVList.append(e2)
        e2=e2.NV
        if starte2==e2:
            break
    for e in e2NVList:
        e.V=Vn
        e.Adjacent().S.V=Vn
    return eN


def Cell(e):
    FaceCounter=1
    starte = e
    DictEdges = {e: ""}
    finish = True
    while finish:
        e = e.NF
        if (starte == e):
            i = -1
            while finish:
                e = list(DictEdges.keys())[i].S
                if (e in DictEdges):
                    i -= 1
                    if (abs(i) > len(DictEdges)):
                        finish = False
                        break
                else:
                    break
            starte = e
            DictEdges[e] = ""
        else:
            FaceCounter+=1
            DictEdges[e] = ""
    return list(DictEdges.keys())


def get_UniqueFace_in_cell(e):
        """Return all unique edges for each face in cell"""
        FaceCounter=1
        UniqueEdgeList=list()
        UniqueEdgeList.append(e)
        starte = e
        DictEdges = {e: ""}
        finish = True
        while finish:
            e = e.NF
            if (starte == e):
                i = -1
                while finish:
                    e = list(DictEdges.keys())[i].S

                    if (e in DictEdges):
                        i -= 1
                        if (abs(i) > len(DictEdges)):
                            finish = False
                            break
                    else:
                        FaceCounter += 1
                        UniqueEdgeList.append(e)
                        break
                starte = e
                DictEdges[e] = ""
            else:
                DictEdges[e] = ""
        return UniqueEdgeList


def is_Face_planar(e, tol=1e-8):
    """
    vertices: array-like of shape (N, 3)
    tol: numerical tolerance
    """
    vertices=list()
    starte=e
    while(True):
        vertices.append((
            float(e.V.x),
            float(e.V.y),
            float(e.V.z)
        ))
        e=e.NF
        if starte==e:
            break
    vertices = np.asarray(vertices)

    if vertices.shape[0] < 3:
        return True  # degenerate but planar

    # Find 3 non-collinear points
    p0 = vertices[0]
    normal = None

    for i in range(1, len(vertices) - 1):
        v1 = vertices[i] - p0
        v2 = vertices[i + 1] - p0
        normal = np.cross(v1, v2)
        # print(normal)
        if np.linalg.norm(normal) > tol:
            break

    if normal is None or np.linalg.norm(normal) < tol:
        return True  # all points collinear → planar

    normal = normal / np.linalg.norm(normal)

    # Check distance of each point from plane
    distances = np.abs(np.dot(vertices - p0, normal))
    return np.all(distances < tol)


def reset_globals():
    """Reset global containers and counters (useful between runs)."""
    global dhe, killdhe
    dhe = []
    killdhe = set()
    Vertex.cntId = 1
    DualHalfEdge.cntId = 1


def Getdhe():

    return dhe, killdhe


def random_rgba(alpha):
    return (
        np.random.randint(0, 256)/256,  # R
        np.random.randint(0, 256)/256,  # G
        np.random.randint(0, 256)/256,  # B
        alpha            # A (0–1)
    )


def make_vertex(x, y, z=0.0, ch=-1, clr=(0, 0, 1), alpha=1):
    if clr==None:
        clr=random_rgba(alpha)
    """Helper to create a Vertex."""
    return Vertex(x, y, z, ch=ch, clr=clr)
