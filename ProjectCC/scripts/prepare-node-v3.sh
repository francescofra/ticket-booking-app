#!/bin/bash
set -e

echo "=========================================="
echo "  K8s Node Preparation (Ubuntu 24.04)"
echo "=========================================="

# ----- Fix IPv4 (evita problemi IPv6 del Learner Lab) -----
echo ">>> [0/8] Fix IPv4..."
echo 'Acquire::ForceIPv4 "true";' | sudo tee /etc/apt/apt.conf.d/99force-ipv4

# ----- 1. Aggiornamento sistema -----
echo ">>> [1/8] Aggiornamento sistema..."
sudo apt-get update -y
sudo apt-get upgrade -y

# ----- 2. Disabilita swap -----
echo ">>> [2/8] Disabilitazione swap..."
sudo swapoff -a
sudo sed -i '/ swap / s/^\(.*\)$/#\1/g' /etc/fstab

# ----- 3. Moduli kernel -----
echo ">>> [3/8] Configurazione moduli kernel..."
cat <<EOF | sudo tee /etc/modules-load.d/k8s.conf
overlay
br_netfilter
EOF
sudo modprobe overlay
sudo modprobe br_netfilter

# ----- 4. Parametri sysctl -----
echo ">>> [4/8] Configurazione sysctl..."
cat <<EOF | sudo tee /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF
sudo sysctl --system

# ----- 5. Installazione containerd -----
echo ">>> [5/8] Installazione containerd..."
sudo apt-get install -y containerd

# ----- 6. Configurazione containerd -----
echo ">>> [6/8] Configurazione containerd..."
sudo mkdir -p /etc/containerd
containerd config default | sudo tee /etc/containerd/config.toml > /dev/null
sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
sudo systemctl restart containerd
sudo systemctl enable containerd

# ----- 7. Installazione kubeadm, kubelet, kubectl -----
echo ">>> [7/8] Installazione kubeadm, kubelet, kubectl..."
sudo apt-get install -y apt-transport-https ca-certificates curl gpg

sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.29/deb/Release.key | \
  sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg

echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.29/deb/ /' | \
  sudo tee /etc/apt/sources.list.d/kubernetes.list

sudo apt-get update -y
sudo apt-get install -y kubelet kubeadm kubectl
sudo apt-mark hold kubelet kubeadm kubectl

# ----- 8. Verifica -----
echo ">>> [8/8] Verifica installazione..."
echo "--- containerd ---"
sudo systemctl is-active containerd
echo "--- kubeadm ---"
kubeadm version --output=short
echo "--- kubectl ---"
kubectl version --client --output=yaml | grep gitVersion
echo "--- kubelet ---"
kubelet --version
echo "--- swap ---"
free -h | grep Swap
echo "--- moduli kernel ---"
lsmod | grep -E 'overlay|br_netfilter'

echo ""
echo "=========================================="
echo "  ✓ Preparazione nodo completata!"
echo "=========================================="