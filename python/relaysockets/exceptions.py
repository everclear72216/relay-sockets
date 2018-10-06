
class RelayException(BaseException):
    pass

class MachineDisconnectedException(RelayException):
    pass

class ServiceDisconnectedException(RelayException):
    pass