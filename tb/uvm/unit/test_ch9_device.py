"""The device software answers standard requests the way Chapter 9 requires."""
from usbtb.ch9.constants import DescriptorType, DeviceState, FeatureSelector, Recipient
from usbtb.ch9.device import Ch9Device, Configure, HaltEndpoint, SelectAlternate, SetAddress, SoftwareFault
from usbtb.ch9.profile import DeviceProfile
from usbtb.ch9.setup import SetupPacket


def _device(seed=3):
    return Ch9Device(DeviceProfile(seed).descriptor_set())


def _configured(device):
    device.complete(device.handle(SetupPacket.set_address(5)).actions[0])
    value = device.descriptors.configurations[0].configuration_value
    device.handle(SetupPacket.set_configuration(value))
    return value


def test_enumeration_walks_the_states():
    device = _device()
    data = device.handle(SetupPacket.get_descriptor(DescriptorType.DEVICE, length=64)).data
    assert data == device.descriptors.device.to_bytes()
    response = device.handle(SetupPacket.set_address(5))
    assert response.actions == (SetAddress(5),) and device.state is DeviceState.DEFAULT
    device.complete(response.actions[0])
    assert device.state is DeviceState.ADDRESS
    assert device.handle(SetupPacket.get_configuration()).data == b"\x00"
    value = device.descriptors.configurations[0].configuration_value
    response = device.handle(SetupPacket.set_configuration(value))
    assert isinstance(response.actions[0], Configure) and device.state is DeviceState.CONFIGURED
    assert device.handle(SetupPacket.get_configuration()).data == bytes((value,))
    device.handle(SetupPacket.set_configuration(0))
    assert device.state is DeviceState.ADDRESS
    device.bus_reset()
    assert device.state is DeviceState.DEFAULT and device.address == 0


def test_descriptor_reads_are_truncated_to_wlength():
    device = _device()
    assert len(device.handle(SetupPacket.get_descriptor(DescriptorType.CONFIGURATION, 0, 9)).data) == 9


def test_request_errors_stall():
    device = _device()
    assert device.handle(SetupPacket.get_descriptor(0x55)).stall
    assert device.handle(SetupPacket.get_descriptor(DescriptorType.DEVICE_QUALIFIER, length=10)).stall
    assert device.handle(SetupPacket.get_descriptor(DescriptorType.BOS, length=5)).stall
    assert device.handle(SetupPacket.set_feature(0x55)).stall
    assert device.handle(SetupPacket.clear_feature(0x55)).stall
    assert device.handle(SetupPacket.set_configuration(99)).stall, "configuration value that does not exist"
    assert device.handle(SetupPacket.set_address(128 & 0x7F | 0x80)).stall


def test_remote_wakeup_feature_and_status():
    device = _device()
    _configured(device)
    device.handle(SetupPacket.set_feature(FeatureSelector.DEVICE_REMOTE_WAKEUP))
    status = int.from_bytes(device.handle(SetupPacket.get_status()).data, "little")
    assert status & 0x2 and device.remote_wakeup_allowed
    device.handle(SetupPacket.clear_feature(FeatureSelector.DEVICE_REMOTE_WAKEUP))
    assert not device.remote_wakeup_allowed


def test_endpoint_halt_round_trip():
    device = next(d for d in (_device(seed) for seed in range(50))
                  if d.descriptors.configurations[0].interfaces[0].endpoints)
    _configured(device)
    endpoint = device.descriptors.configurations[0].interfaces[0].endpoints[0]
    address = endpoint.endpoint_address
    response = device.handle(SetupPacket.set_feature(FeatureSelector.ENDPOINT_HALT, Recipient.ENDPOINT, address))
    assert response.actions == (HaltEndpoint(endpoint, True),)
    assert device.handle(SetupPacket.get_status(Recipient.ENDPOINT, address)).data == b"\x01\x00"
    device.handle(SetupPacket.clear_feature(FeatureSelector.ENDPOINT_HALT, Recipient.ENDPOINT, address))
    assert device.handle(SetupPacket.get_status(Recipient.ENDPOINT, address)).data == b"\x00\x00"


def test_set_interface_selects_alternates():
    device = next(d for d in (_device(seed) for seed in range(80))
                  if any(i.descriptor.alternate_setting for i in d.descriptors.configurations[0].interfaces))
    _configured(device)
    interface = next(i.descriptor for i in device.descriptors.configurations[0].interfaces
                     if i.descriptor.alternate_setting)
    response = device.handle(SetupPacket.set_interface(interface.interface_number, interface.alternate_setting))
    assert isinstance(response.actions[0], SelectAlternate)
    data = device.handle(SetupPacket.get_interface(interface.interface_number)).data
    assert data == bytes((interface.alternate_setting,))


def test_faults_change_behaviour():
    device = Ch9Device(DeviceProfile(1).descriptor_set(), frozenset({SoftwareFault.NO_STALL_ON_BAD_REQUEST,
                                                                     SoftwareFault.BAD_DEVICE_DESCRIPTOR_LENGTH}))
    assert not device.handle(SetupPacket.get_descriptor(0x55)).stall
    assert device.handle(SetupPacket.get_descriptor(DescriptorType.DEVICE, length=18)).data[0] == 17
