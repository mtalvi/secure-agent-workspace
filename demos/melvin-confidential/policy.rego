# Recording policy: block host-driven exec and policy replacement. This is
# deliberately a lifecycle policy, not an attested workload/image allowlist.
package agent_policy

import future.keywords.in
import future.keywords.if

default AddARPNeighborsRequest := true
default AddSwapRequest := true
default CloseStdinRequest := true
default CreateSandboxRequest := true
default DestroySandboxRequest := true
default GetMetricsRequest := true
default GetOOMEventRequest := true
default GuestDetailsRequest := true
default ListInterfacesRequest := true
default ListRoutesRequest := true
default MemHotplugByProbeRequest := true
default OnlineCPUMemRequest := true
default PauseContainerRequest := true
default PullImageRequest := true
default RemoveContainerRequest := true
default RemoveStaleVirtiofsShareMountsRequest := true
default ReseedRandomDevRequest := true
default ResumeContainerRequest := true
default SetGuestDateTimeRequest := true
default SignalProcessRequest := true
default StartContainerRequest := true
default StartTracingRequest := true
default StatsContainerRequest := true
default StopTracingRequest := true
default TtyWinResizeRequest := true
default UpdateContainerRequest := true
default UpdateEphemeralMountsRequest := true
default UpdateInterfaceRequest := true
default UpdateRoutesRequest := true
default WaitProcessRequest := true
default WriteStreamRequest := true
default CreateContainerRequest := true
# Container logs remain available; the recording claim is specifically about
# host-driven exec. Applications must not log credentials.
default ReadStreamRequest := true
default CopyFileRequest := false
default SetPolicyRequest := false
default ExecProcessRequest := false

# Limit kubelet projected-file delivery to the Kata shared container paths;
# reject traversal and absolute symlink targets. Never log file contents.
CopyFileRequest if {
    input.file_type in {"Regular", "Directory"}
    allowed_path(input.path)
}
CopyFileRequest if {
    input.file_type == "Symlink"
    allowed_path(input.path)
    not regex.match("(^|/)\\.\\.($|/)", input.symlink_target)
    not startswith(input.symlink_target, "/")
}
allowed_path(path) if {
    not regex.match("(^|/)\\.\\.($|/)", path)
    regex.match("^/run/kata-containers/shared/containers(/passthrough)?/(watchable/)?[a-z0-9]{64}-[a-z0-9]{16}-", path)
}
