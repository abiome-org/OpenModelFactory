import pytest
from omf.deployments import DeploymentService, DeploymentSpec
from omf.errors import ConflictError


class Adapter:
    def apply(self, spec, revision):
        return revision

    def rollback(self, revision):
        return revision


def test_deployment_cas_and_rollback():
    service = DeploymentService(Adapter())
    spec = DeploymentSpec("release", "runtime", "service")
    first = service.deploy("api", spec, "r1")
    with pytest.raises(ConflictError):
        service.deploy("api", spec, "r2")
    second = service.deploy("api", spec, "r2", expected_version=first.version)
    rolled = service.rollback("api", expected_version=second.version)
    assert rolled.desired_revision == "r1"
    assert rolled.state == "rolled_back"
