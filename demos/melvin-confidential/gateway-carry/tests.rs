// Included inside the downstream driver's existing tests module by test.sh.
const DEMO_INITDATA: &str = "io.katacontainers.config.hypervisor.cc_init_data";
const DEMO_SIZE: &str = "io.katacontainers.config.hypervisor.machine_type";

#[test]
fn demo_workload_annotations_override_caller_and_keep_default_empty() {
    let template = template_with_pod_annotations(&[DEMO_INITDATA]);
    let annotations = BTreeMap::from([
        (DEMO_INITDATA.to_string(), "approved-policy".to_string()),
        (DEMO_SIZE.to_string(), "Standard_DC4as_v5".to_string()),
    ]);
    let params = SandboxPodParams {
        default_runtime_class_name: "kata-cc",
        workload_pod_annotations: Some(&annotations),
        ..SandboxPodParams::default()
    };
    let pod = sandbox_template_to_k8s(&template, false, &std::collections::HashMap::new(), true, &params);
    assert_eq!(pod["metadata"]["annotations"][DEMO_INITDATA], "approved-policy");
    assert_eq!(pod["metadata"]["annotations"][DEMO_SIZE], "Standard_DC4as_v5");
    let ordinary = sandbox_template_to_k8s(&SandboxTemplate::default(), false,
        &std::collections::HashMap::new(), true, &SandboxPodParams::default());
    assert!(ordinary["metadata"]["annotations"][DEMO_INITDATA].is_null());
    assert!(ordinary["spec"]["runtimeClassName"].is_null());
}

#[tokio::test]
async fn demo_supervisor_creation_sends_admin_annotations_and_class() {
    let annotations = BTreeMap::from([
        (DEMO_INITDATA.to_string(), "approved-policy".to_string()),
        (DEMO_SIZE.to_string(), "Standard_DC2as_v5".to_string()),
    ]);
    let config = KubernetesComputeConfig {
        supervisor_runtime_class_name: "kata-cc".into(),
        supervisor_pod_annotations: annotations.clone(),
        ..KubernetesComputeConfig::default()
    };
    let (driver, steps, bodies) = scripted_driver(config, vec![(
        http::Method::POST, "/api/v1/namespaces/openshell/pods",
        kube_test_response(http::StatusCode::CREATED, serde_json::json!({
            "apiVersion": "v1", "kind": "Pod", "metadata": {"name": "supervisor", "uid": "new-uid"}
        })),
    )]);
    let uid = driver.create_sandbox_runtime_supervisor("openshell", &SandboxRuntimeNames::new("demo"),
        "demo", "notebook", pod_owner("sandbox", "sandbox-uid"), 1000, 1000, "{}", "info", false)
        .await.unwrap();
    assert_eq!(uid, "new-uid");
    assert!(steps.lock().unwrap().is_empty());
    let requests = bodies.lock().unwrap();
    assert_eq!(requests[0]["spec"]["runtimeClassName"], "kata-cc");
    for (key, value) in annotations {
        assert_eq!(requests[0]["metadata"]["annotations"][key], value);
    }
}

#[test]
fn demo_guest_annotation_config_rejects_other_keys_and_unplaced_pods() {
    let mut config = KubernetesComputeConfig::default();
    assert!(config.workload_pod_annotations.is_empty());
    assert!(config.supervisor_pod_annotations.is_empty());
    config.workload_pod_annotations.insert(DEMO_INITDATA.into(), "policy".into());
    assert!(config.validate_configuration().is_err());
    config.default_runtime_class_name = "kata-cc".into();
    config.workload_pod_annotations.insert("unapproved.example/key".into(), "value".into());
    assert!(config.validate_configuration().is_err());
}
