import numpy as np
from scipy.integrate import quad

from aqc.aqc import config
from aqc.grid import RectGrid
from aqc.utils import ifft2


class Default:
    def __init__(self, default_path):
        self._default_path_list = default_path.split(".")

    def __get__(self, obj, cls):
        current_node = obj
        for node in self._default_path_list:
            current_node = getattr(current_node, node)
        return current_node
        

class PhaseScreen():
  wvl = Default("channel.source.wvl")
  grid = Default("channel.grid")
  
  def __init__(self, model, thickness=None, wvl=None, grid=None):
    self.model = model
    self.thickness = thickness
    if wvl: 
      self.wvl = wvl
    if grid:
      self.grid = grid

  def generate_phase_screen(self):
    """Return complex phase screen"""
    raise NotImplementedError

  def generate(self, complex=False):
    if complex:
      return self.generate_phase_screen()
    return self.generate_phase_screen().real
  
  def generator(self):
    while True:
      ps = self.generate(complex=True)
      yield ps.real
      yield ps.imag
  

class FFTPhaseScreen(PhaseScreen):
  def __init__(self, subharmonics, *args, **kwargs):
    self.subharmonics = subharmonics
    super().__init__(*args, **kwargs)

  def generate_phase_screen(self):
    xp = self.grid.get_array_module()
    def get_cn_coefficients(cn_f_grid):
      cn = (xp.random.normal(size=cn_f_grid.shape) + 1j * xp.random.normal(size=cn_f_grid.shape)).astype(config["dtype"]["complex"]) * \
            xp.sqrt(self.model.psd_phi_f(cn_f_grid.get_rho(), 2 * xp.pi / self.wvl, self.thickness)) * 2 * xp.pi * cn_f_grid.delta
      cn[cn_f_grid.origin_index] = 0
      return cn
    
    f_grid = self.grid.get_f_grid()
    phase_screen = ifft2(get_cn_coefficients(f_grid), 1)

    for sh in range(self.subharmonics):
      sh_f_grid = RectGrid(3, f_grid.delta / 3**(sh + 1))
      cn = get_cn_coefficients(sh_f_grid)

      # fx, fy = sh_f_grid.get_xy()
      # return xp.exp(1j * 2 * xp.pi * self.grid.get_y() @ fy.T) @ cn @ xp.exp(1j * 2 * xp.pi * fx.T @ self.grid.get_x())
      f = sh_f_grid.get_x()
      for i in range(sh_f_grid.resolution[0]):
        for j in range(sh_f_grid.resolution[1]):
          phase_screen = phase_screen + cn[i,j] * xp.exp(1j * 2 * xp.pi * (f[0, i] * self.grid.get_x() + f[0, j] * self.grid.get_y()))
    
    return phase_screen - xp.mean(phase_screen)
  

class SSPhaseScreen(PhaseScreen):
  def __init__(self, f_grid, *args, **kwargs):
    self.f_grid = f_grid
    super().__init__(self, *args, **kwargs)

  def __post_init__(self):
    self._sqrt_int_spectrum = None
  
  @property
  def sqrt_int_spectrum(self):
    if not self._sqrt_int_spectrum is None:
      return self._sqrt_int_spectrum
    
    xp = self.grid.get_array_module()
    f = self.f_grid.base
    self._sqrt_int_spectrum = xp.empty(self.f_grid.points)
    in_int_function = lambda f: (2 * np.pi)**2 * f * self.model.psd_phi_f(f, 2 * xp.pi / self.wvl, self.thickness)

    for i in range(self.f_grid.points):
      f_prev = f[i - 1] if i != 0 else 0
      self._sqrt_int_spectrum[i] = xp.sqrt(2 * np.pi * quad(in_int_function, f_prev, f[i])[0])
    
    return self._sqrt_int_spectrum

  def generate_phase_screen(self):
    xp = self.grid.get_array_module()
    
    cn = (xp.array([1, 1j]) @ xp.random.normal(size=(2, self.f_grid.points))).astype(config["dtype"]["complex"]) * self.sqrt_int_spectrum

    rho = self.f_grid.get_rho()
    theta = self.f_grid.get_theta()
    fx, fy = self.f_grid.get_xy(rho, theta)
    return xp.exp(1j * 2 * xp.pi * self.grid.get_y() @ fy.T) @ xp.diag(cn) @ xp.exp(1j * 2 * xp.pi * fx.T @ self.grid.get_x())

  

class SUPhaseScreen(PhaseScreen):
  def __init__(self, f_grid, *args, **kwargs):
    self.f_grid = f_grid
    super().__init__(*args, **kwargs)
    self._delta_k_base = None
  
  @property
  def delta_k_base(self):
    xp = self.grid.get_array_module()
    if self._delta_k_base is None:
      self._delta_k_base = (2 * xp.pi)**2 * xp.array((self.f_grid.base**2 - np.insert(self.f_grid.base, 0, 0)[:-1]**2), dtype=config["dtype"]["float"])
    return self._delta_k_base

  def generate_phase_screen(self):
    xp = self.grid.get_array_module()

    rho = self.f_grid.get_rho()
    theta = self.f_grid.get_theta()
    
    cn = (xp.array([1, 1j]) @ xp.random.normal(size=(2, self.f_grid.points))).astype(config["dtype"]["complex"]) * \
      xp.sqrt(self.model.psd_phi_f(rho, 2 * xp.pi / self.wvl, self.thickness) * xp.pi * self.delta_k_base) 

    fx, fy = self.f_grid.get_xy(rho, theta)
    return xp.exp(1j * 2 * xp.pi * self.grid.get_y() @ fy.T) @ xp.diag(cn) @ xp.exp(1j * 2 * xp.pi * fx.T @ self.grid.get_x())


class WindSUPhaseScreen(PhaseScreen):
  def __init__(self, f_grid, speed, *args, **kwargs):
    self.f_grid = f_grid
    self.speed = speed
    super().__init__(*args, **kwargs)
    self.cnp = None

  def generate_cn(self):
    self.rho = self.f_grid.get_rho()
    self.theta = self.f_grid.get_theta()
    xp = self.grid.get_array_module()
    self.cnp = (xp.array([1, 1j]) @ xp.random.normal(size=(2, self.f_grid.points)).astype(config["dtype"]["complex"]))
    self.iteration = 0

  def generate_phase_screen(self):
    if self.cnp is None:
      self.generate_cn()

    xp = self.grid.get_array_module()

    cn = self.cnp * \
      xp.sqrt(self.model.psd_phi_f(self.rho, 2 * xp.pi / self.wvl, self.thickness) * \
      xp.pi * (2 * xp.pi)**2 * xp.array(self.f_grid.base**2 - np.insert(self.f_grid.base, 0, 0)[:-1]**2, dtype=config["dtype"]["float"])) 
    
    fx, fy = self.f_grid.get_xy(self.rho, self.theta)
    offset = self.iteration * self.speed
    self.iteration += 1
    return xp.exp(1j * 2 * xp.pi * self.grid.get_y() @ fy.T) @ xp.diag(cn) @ xp.exp(1j * 2 * xp.pi * fx.T @ (self.grid.get_x() + offset))

  def generator(self):
    while True:
      yield self.generate(complex=False)
  