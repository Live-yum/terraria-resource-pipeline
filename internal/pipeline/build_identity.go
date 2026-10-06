package pipeline

import (
    "runtime/debug"
    "regexp"
)

// BuildCommit is supplied by source-controlled builds when VCS metadata is not
// embedded (for example the Docker build context). Never infer a source identity
// from a release version string. Empty identity cannot pass the assembly gate.
var BuildCommit string
var BuildClean = "false"

func extractorIdentity() (commit string, clean bool) {
    if info, ok := debug.ReadBuildInfo(); ok {
        var modified string
        for _, setting := range info.Settings {
            if setting.Key == "vcs.revision" { commit = setting.Value }
            if setting.Key == "vcs.modified" { modified = setting.Value }
        }
        if regexp.MustCompile(`^[0-9a-f]{40}$`).MatchString(commit) {
            return commit, modified == "false"
        }
    }
    // The controlled build wrapper sets these only after an exact clean-checkout
    // preflight. The release gate still requires independent build evidence.
    if regexp.MustCompile(`^[0-9a-f]{40}$`).MatchString(BuildCommit) { return BuildCommit, BuildClean == "true" }
    return "", false
}
