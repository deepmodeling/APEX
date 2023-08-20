import numpy as np

from dflow.python import upload_packages
upload_packages.append(__file__)

cartesian_ortho_norm_basis = np.array([ [1.0, 0.0, 0.0], \
                                        [0.0, 1.0, 0.0], \
                                        [0.0, 0.0, 1.0] ])

def plane_miller_bravais_to_miller(miller_bravais_plane):
    '''
    Convert a plane given in Miller-Bravais notation to Miller notation.
    '''

    # A plane in miller bravais notation is (h, k, i, l)
    h = miller_bravais_plane[0]
    k = miller_bravais_plane[1]
    i = miller_bravais_plane[2]
    l = miller_bravais_plane[3]

    # The plane converted to Miller notation (H, K, L)
    H = h
    K = k
    L = l

    return tuple(np.array([H, K, L], dtype='int_'))


def direction_miller_bravais_to_miller(miller_bravais_dir):
    '''
    Convert a direction given in Miller-Bravais notation to Miller notation.
    '''

    # The direction in Miller-Bravais notation is [U, V, T, W]
    U = miller_bravais_dir[0]
    V = miller_bravais_dir[1]
    T = miller_bravais_dir[2]
    W = miller_bravais_dir[3]

    # The direction converted to Miller notation [u, v, w]
    u = 2*U + V # U - t or 2*U + V
    v = 2*V + U # V - T or 2*V + U
    w = W

    return tuple(np.array([u, v, w], dtype='int_'))

def trans_mat_basis(dest, src=cartesian_ortho_norm_basis ):
    '''
    This matrix will transform any vector represented in basis
    A to a representation in basis B
    '''
    rmat = np.zeros((3, 3))

    rmat[0][0] = np.dot(dest[0], src[0])
    rmat[0][1] = np.dot(dest[0], src[1])
    rmat[0][2] = np.dot(dest[0], src[2])

    rmat[1][0] = np.dot(dest[1], src[0])
    rmat[1][1] = np.dot(dest[1], src[1])
    rmat[1][2] = np.dot(dest[1], src[2])

    rmat[2][0] = np.dot(dest[2], src[0])
    rmat[2][1] = np.dot(dest[2], src[1])
    rmat[2][2] = np.dot(dest[2], src[2])

    return rmat
