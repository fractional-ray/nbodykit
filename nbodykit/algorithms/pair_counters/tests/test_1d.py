from runtests.mpi import MPITest
from nbodykit.lab import *
from nbodykit import setup_logging

from numpy.testing import assert_array_equal, assert_allclose
import kdcount.correlate as correlate
import os
import pytest

setup_logging()

def gather_data(source, name):
    return numpy.concatenate(source.comm.allgather(source[name].compute()), axis=0)

def generate_sim_data(seed):
    return UniformCatalog(nbar=3e-6, BoxSize=512., seed=seed)

def generate_survey_data(seed):
    cosmo = cosmology.Planck15
    s = RandomCatalog(1000, seed=seed)

    s['Redshift'] = s.rng.normal(loc=0.5, scale=0.1, size=s.size)
    s['RA'] = s.rng.uniform(low=110, high=260, size=s.size)
    s['DEC'] = s.rng.uniform(low=-3.6, high=60., size=s.size)
    s['Position'] = transform.SkyToCartesian(s['RA'], s['DEC'], s['Redshift'], cosmo=cosmo)

    return s

def reference_paircount(pos1, w1, redges, boxsize, pos2=None, w2=None, los=2):
    """Reference pair counting via kdcount"""
    # make the trees
    tree1 = correlate.points(pos1, boxsize=boxsize, weights=w1)
    if pos2 is None:
        tree2 = tree1
    else:
        tree2 = correlate.points(pos2, boxsize=boxsize, weights=w2)

    # do the paircount
    bins = correlate.RBinning(redges)
    pc = correlate.paircount(tree1, tree2, bins, np=0, usefast=False, compute_mean_coords=True)
    return numpy.nan_to_num(pc.pair_counts), numpy.nan_to_num(pc.mean_centers), pc.sum1

@MPITest([1, 3])
def test_sim_periodic_auto(comm):
    CurrentMPIComm.set(comm)

    # uniform source of particles
    source = generate_sim_data(seed=42)

    # add some weights b/w 0 and 1
    source['Weight'] = source.rng.uniform(size=len(source))

    # make the bin edges
    redges = numpy.linspace(10, 150, 10)

    # do the weighted paircount
    r = SimulationBoxPairCount('1d', source, redges, periodic=True, weight='Weight')

    # cannot compute r=0
    with pytest.raises(ValueError):
        r = SimulationBoxPairCount('1d', source, numpy.linspace(0, 10.0, 10))

    pos = gather_data(source, "Position")
    w = gather_data(source, "Weight")

    # verify with kdcount
    npairs, ravg, wsum = reference_paircount(pos, w, redges, source.attrs['BoxSize'])
    assert_allclose(ravg, r.pairs['r'])
    assert_allclose(npairs, r.pairs['npairs'])
    assert_allclose(wsum, r.pairs['npairs'] * r.pairs['weightavg'])

    # test save
    r.save('paircount-test.json')
    r2 = SimulationBoxPairCount.load('paircount-test.json')
    assert_array_equal(r.pairs.data, r2.pairs.data)

    if comm.rank == 0: os.remove('paircount-test.json')

@MPITest([1, 3])
def test_sim_nonperiodic_auto(comm):
    CurrentMPIComm.set(comm)

    # uniform source of particles
    source = generate_sim_data(seed=42)
    source['Weight'] = numpy.random.random(size=len(source))

    # make the bin edges
    redges = numpy.linspace(10, 150, 10)

    # do the weighted paircount
    r = SimulationBoxPairCount('1d', source, redges, periodic=False, weight='Weight')

    pos = gather_data(source, "Position")
    w = gather_data(source, "Weight")

    # verify with kdcount
    npairs, ravg, wsum = reference_paircount(pos, w, redges, None)
    assert_allclose(ravg, r.pairs['r'])
    assert_allclose(npairs, r.pairs['npairs'])
    assert_allclose(wsum, r.pairs['npairs'] * r.pairs['weightavg'])


@MPITest([1, 3])
def test_sim_periodic_cross(comm):

    CurrentMPIComm.set(comm)

    # generate data
    first = generate_sim_data(seed=42)
    second = generate_sim_data(seed=84)

    # make the bin edges
    redges = numpy.linspace(10, 150, 10)

    # do the paircount
    r = SimulationBoxPairCount('1d', first, redges, second=second, periodic=True)

    pos1 = gather_data(first, "Position")
    pos2 = gather_data(second, "Position")

    # verify with kdcount
    npairs, ravg, wsum = reference_paircount(pos1, None, redges, first.attrs['BoxSize'], pos2=pos2)
    assert_allclose(ravg, r.pairs['r'])
    assert_allclose(npairs, r.pairs['npairs'])
    assert_allclose(wsum, r.pairs['npairs'] * r.pairs['weightavg'])

@MPITest([1])
def test_bad_los(comm):

    CurrentMPIComm.set(comm)
    source = generate_sim_data(seed=42)
    redges = numpy.linspace(10, 150, 10)

    # should be 'x', 'y', 'z'
    with pytest.raises(AssertionError):
        r = SimulationBoxPairCount('1d', source, redges, los='a')

    # should be [0,1,2]
    with pytest.raises(AssertionError):
        r = SimulationBoxPairCount('1d', source, redges, los=3)

    # negative okay
    r = SimulationBoxPairCount('1d', source, redges, los=-1)

    # vector is bad
    with pytest.raises(ValueError):
        r = SimulationBoxPairCount('1d', source, redges, los=[0,0,1])

@MPITest([1])
def test_bad_rmax(comm):

    CurrentMPIComm.set(comm)
    source = generate_sim_data(seed=42)
    redges = numpy.linspace(10, 400., 10)

    # rmax is too big
    with pytest.raises(ValueError):
        r = SimulationBoxPairCount('1d', source, redges, periodic=True)

@MPITest([1])
def test_noncubic_box(comm):

    CurrentMPIComm.set(comm)
    source = UniformCatalog(nbar=3e-6, BoxSize=[512., 512., 256], seed=42)
    redges = numpy.linspace(10, 100., 10)

    # cannot do non-cubic boxes
    with pytest.raises(NotImplementedError):
        r = SimulationBoxPairCount('1d', source, redges, periodic=True)

@MPITest([1])
def test_bad_boxsize(comm):

    CurrentMPIComm.set(comm)

    # uniform source of particles
    first = UniformCatalog(nbar=3e-5, BoxSize=512., seed=42)
    second = UniformCatalog(nbar=3e-5, BoxSize=256., seed=42)

    # make the bin edges
    redges = numpy.linspace(10, 50., 10)

    # box sizes are different
    with pytest.raises(ValueError):
        r = SimulationBoxPairCount('1d', first, redges, second=second)

    # specified different value
    with pytest.raises(ValueError):
        r = SimulationBoxPairCount('1d', second, redges, BoxSize=300.)

    # no boxSize
    del first.attrs['BoxSize']
    with pytest.raises(ValueError):
        r = SimulationBoxPairCount('1d', first, redges)

@MPITest([1, 4])
def test_survey_auto(comm):

    cosmo = cosmology.Planck15
    CurrentMPIComm.set(comm)

    # random particles
    source = generate_survey_data(seed=42)

    # add some weights b/w 0 and 1
    source['Weight'] = source.rng.uniform(size=len(source))

    # make the bin edges
    redges = numpy.linspace(10, 150, 10)

    # do the weighted paircount
    r = SurveyDataPairCount('1d', source, redges, cosmo, weight='Weight')

    # cannot compute r=0
    with pytest.raises(ValueError):
        r = SurveyDataPairCount('1d', source, numpy.linspace(0, 10.0, 10), cosmo)

    pos = gather_data(source, 'Position')
    w = gather_data(source, 'Weight')

    # verify with kdcount
    npairs, ravg, wsum = reference_paircount(pos, w, redges, None)
    assert_allclose(ravg, r.pairs['r'])
    assert_allclose(npairs, r.pairs['npairs'])
    assert_allclose(wsum, r.pairs['npairs'] * r.pairs['weightavg'])

@MPITest([1, 4])
def test_survey_cross(comm):

    cosmo = cosmology.Planck15
    CurrentMPIComm.set(comm)

    # random particles
    first = generate_survey_data(seed=42)
    first['Weight'] = first.rng.uniform(size=first.size)
    second = generate_survey_data(seed=84)
    second['Weight'] = second.rng.uniform(size=second.size)

    # make the bin edges
    redges = numpy.linspace(10, 150, 10)

    # do the paircount
    r = SurveyDataPairCount('1d', first, redges, cosmo, second=second)

    pos1 = gather_data(first, 'Position')
    pos2 = gather_data(second, 'Position')
    w1 = gather_data(first, 'Weight')
    w2 = gather_data(second, 'Weight')

    # verify with kdcount
    npairs, ravg, wsum = reference_paircount(pos1, w1, redges, None, pos2=pos2, w2=w2)
    assert_allclose(ravg, r.pairs['r'])
    assert_allclose(npairs, r.pairs['npairs'])
    assert_allclose(wsum, r.pairs['npairs'] * r.pairs['weightavg'])

@MPITest([1])
def test_survey_missing_columns(comm):
    CurrentMPIComm.set(comm)
    source = generate_survey_data(seed=42)
    redges = numpy.linspace(10, 150, 10)

    # missing column
    with pytest.raises(ValueError):
        r = SurveyDataPairCount('1d', source, redges, cosmology.Planck15, ra='BAD')

@MPITest([1])
def test_bad_mode(comm):
    CurrentMPIComm.set(comm)
    source = generate_survey_data(seed=42)
    redges = numpy.linspace(10, 150, 10)

    # missing column
    with pytest.raises(ValueError):
        r = SurveyDataPairCount('bad mode', source, redges, cosmology.Planck15)

@MPITest([1, 4])
def test_corrfunc_exception(comm):

    CurrentMPIComm.set(comm)
    pos = numpy.zeros((100,3))
    cat = ArrayCatalog({'Position':pos})

    redges = numpy.linspace(0.01, 0.1, 10)

    # corrfunc will throw an error due to bad input data
    with pytest.raises(Exception):
        r = SimulationBoxPairCount('1d', cat, redges, periodic=False, BoxSize=[1., 1., 1,])

@MPITest([1, 4])
def test_missing_corrfunc(comm):

    from nbodykit.algorithms.pair_counters.corrfunc.base import MissingCorrfuncError
    CurrentMPIComm.set(comm)

    with pytest.raises(Exception):
        raise MissingCorrfuncError()
