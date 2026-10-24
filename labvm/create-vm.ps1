# create-vm.ps1 - the STASIS lab VM in VirtualBox, to the build guide's spec:
# Ubuntu 26.04 (kernel 7.0, sched_ext), 4 GB FIXED memory, 4 vCPUs, 40 GB disk.
# Network: NAT, with SSH and the dashboard forwarded to 127.0.0.1 only, so
# nothing on the LAN can reach the VM. First boot is configured by cloud-init
# from seed\seed.iso (user "stasis", key-only SSH).
$ErrorActionPreference = "Stop"
$vb    = "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
$root  = "C:\Users\ragul\stasis-vm"
$name  = "stasis-lab"
$ova   = "$root\downloads\ubuntu-26.04-server-cloudimg-amd64.ova"
$seed  = "$root\seed\seed.iso"
$vms   = "$root\vms"
$vdi   = "$vms\$name\$name-disk.vdi"

function vbm { & $vb @args; if ($LASTEXITCODE -ne 0) { throw "VBoxManage $($args -join ' ') failed ($LASTEXITCODE)" } }

if ((& $vb list vms) -match "`"$name`"") { throw "a VM named $name already exists; not overwriting it" }

"== import the Ubuntu cloud image"
vbm import $ova --vsys 0 --vmname $name --basefolder $vms --memory 4096 --cpus 4

"== disk: convert to VDI and grow to 40 GB (the image ships ~3.5 GB; cloud-init grows the root filesystem to fit)"
$info = & $vb showvminfo $name --machinereadable
$line = $info | Where-Object { $_ -match '^"(.+)-(\d+)-(\d+)"="(.+\.vmdk)"$' } | Select-Object -First 1
if (-not $line) { throw "could not find the imported vmdk in showvminfo" }
$null = $line -match '^"(.+)-(\d+)-(\d+)"="(.+\.vmdk)"$'
$ctl, $port, $dev, $vmdk = $Matches[1], $Matches[2], $Matches[3], $Matches[4]
"   imported disk: $vmdk on $ctl port $port device $dev"
vbm clonemedium disk $vmdk $vdi --format VDI
vbm storageattach $name --storagectl $ctl --port $port --device $dev --type hdd --medium $vdi
vbm closemedium disk $vmdk --delete
vbm modifymedium disk $vdi --resize 40960

"== cloud-init seed as a DVD on its own IDE controller"
vbm storagectl $name --name "IDE-seed" --add ide
vbm storageattach $name --storagectl "IDE-seed" --port 0 --device 0 --type dvddrive --medium $seed

"== machine settings"
# Fixed memory (the guide: Dynamic Memory off); no audio or USB; a serial
# console logged to a file - Ubuntu cloud images expect ttyS0, and the log
# is how boot problems get diagnosed on a headless VM.
vbm modifyvm $name --memory 4096 --cpus 4 --audio-enabled off --usb-ohci off `
    --uart1 0x3F8 4 --uart-mode1 file "$vms\$name\serial.log"
vbm modifyvm $name --nic1 nat `
    --nat-pf1 "ssh,tcp,127.0.0.1,2222,,22" `
    --nat-pf1 "dashboard,tcp,127.0.0.1,8687,,8686"

"== start headless"
vbm startvm $name --type headless
"done: $name started"
