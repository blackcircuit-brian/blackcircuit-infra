# Kubernetes Bootstrap (kubeadm + containerd) --- Ubuntu 24.04

## Scope and intent

This procedure bootstraps a **single-node** Kubernetes cluster using
**kubeadm + kubelet + containerd** on Ubuntu 24.04. It is intended as a
baseline cluster to run GitOps-managed platform components.

**Non-goals** - Provisioning hosts/VMs (out of scope) - HA control plane
/ external etcd - Production-grade ingress/LB/storage

## Target environment

-   OS: Ubuntu 24.04 (amd64; same flow applies to arm64/Raspberry Pi)
-   Runtime: containerd (systemd cgroups)
-   Kubernetes: v1.30.14
-   CNI: Calico
-   Pod CIDR: `10.244.0.0/16`

## Prerequisites

-   Host has a **stable LAN IP** (recommend DHCP reservation)
-   Swap **disabled**
-   Internet egress allowed to pull images/manifests

------------------------------------------------------------------------

## 1. Host prep (kernel modules + sysctls)

``` bash
sudo -i

cat >/etc/modules-load.d/k8s.conf <<'EOF'
overlay
br_netfilter
EOF

modprobe overlay
modprobe br_netfilter

cat >/etc/sysctl.d/99-kubernetes-cri.conf <<'EOF'
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF

sysctl --system
```

Validate:

``` bash
lsmod | egrep 'overlay|br_netfilter'
sysctl net.bridge.bridge-nf-call-iptables net.ipv4.ip_forward
```

------------------------------------------------------------------------

## 2. Disable swap (required)

``` bash
sudo swapoff -a
swapon --show
```

Make persistent (if `/swap.img` exists):

``` bash
sudo systemctl mask swap.img.swap 2>/dev/null || true
sudo sed -i 's@^/swap.img@#/swap.img@' /etc/fstab 2>/dev/null || true
sudo rm -f /swap.img
```

------------------------------------------------------------------------

## 3. Install containerd (systemd cgroups)

``` bash
sudo apt-get update
sudo apt-get install -y containerd

sudo mkdir -p /etc/containerd
containerd config default | sudo tee /etc/containerd/config.toml >/dev/null

sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml

sudo systemctl restart containerd
sudo systemctl enable containerd
```

Validate:

``` bash
systemctl status containerd --no-pager
containerd --version
```

------------------------------------------------------------------------

## 4. Install kubeadm/kubelet/kubectl

``` bash
sudo apt-get update
sudo apt-get install -y apt-transport-https ca-certificates curl gpg

sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.30/deb/Release.key   | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg

echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.30/deb/ /" | sudo tee /etc/apt/sources.list.d/kubernetes.list

sudo apt-get update
sudo apt-get install -y kubelet kubeadm kubectl
sudo apt-mark hold kubelet kubeadm kubectl
```

Validate:

``` bash
kubeadm version
kubectl version --client
```

------------------------------------------------------------------------

## 5. kubeadm init

### 5.1 Create kubeadm config

Update `advertiseAddress` and `name` to match the host.

`kubeadm-config.yaml`:

``` yaml
apiVersion: kubeadm.k8s.io/v1beta3
kind: ClusterConfiguration
kubernetesVersion: v1.30.14
networking:
  podSubnet: "10.244.0.0/16"
---
apiVersion: kubeadm.k8s.io/v1beta3
kind: InitConfiguration
localAPIEndpoint:
  advertiseAddress: 192.168.2.95
  bindPort: 6443
nodeRegistration:
  name: vulcan
  criSocket: "unix:///run/containerd/containerd.sock"
  kubeletExtraArgs:
    cgroup-driver: systemd
```

### 5.2 Initialize control plane

``` bash
sudo kubeadm init --config kubeadm-config.yaml
```

### 5.3 Configure kubectl access

``` bash
mkdir -p $HOME/.kube
sudo cp -i /etc/kubernetes/admin.conf $HOME/.kube/config
sudo chown $(id -u):$(id -g) $HOME/.kube/config
```

Validate API reachability:

``` bash
kubectl cluster-info
kubectl get nodes -o wide
```

At this point the node may be `NotReady` until CNI is installed.

------------------------------------------------------------------------

## 6. Install CNI (Calico)

``` bash
kubectl apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.28.2/manifests/calico.yaml
```

Watch:

``` bash
kubectl -n kube-system get pods -w
kubectl get nodes -o wide
```

Expected: node becomes `Ready`, CoreDNS becomes `Running`.

------------------------------------------------------------------------

## 7. Single-node scheduling (optional)

Allow workloads on the control-plane node:

``` bash
kubectl taint nodes --all node-role.kubernetes.io/control-plane- || true
```

------------------------------------------------------------------------

## 8. Sanity test (DNS)

``` bash
kubectl run dns-test --image=busybox:1.36 --restart=Never --command -- sleep 3600
kubectl wait --for=condition=Ready pod/dns-test --timeout=120s
kubectl exec -it dns-test -- nslookup kubernetes.default
kubectl delete pod dns-test
```

------------------------------------------------------------------------

## Troubleshooting

### Ports already in use (`6443`, `10257`, `10259`, `2380`)

Cause: leftover k0s/k3s/k3d processes or prior kubeadm attempt.

Fix: - Stop/disable offending services - Confirm clean:
`bash   sudo ss -lntp | egrep '(:6443|:10259|:10257|:2379|:2380)\b' || true`

### kubelet fails: "running with swap on is not supported"

Fix: disable swap (Section 2). Verify `/proc/swaps` is empty.

### kubelet "Unauthorized" + node never registers

Cause: stale kubelet bootstrap credentials from a prior partial init.

Fix: - `kubeadm reset -f` - wipe `/var/lib/kubelet` +
`/etc/kubernetes/*kubelet*` and retry.

### CoreDNS Pending

Cause: no CNI installed yet. Install Calico (Section 6).
