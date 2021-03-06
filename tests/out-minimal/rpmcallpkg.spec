%if %{with kde4}
%package -n %{name}-client-kde4
Summary:        KDE 4 Backend for sflphone
Group:          Productivity/Telephony/SIP/Clients
Requires:       %{name} = %{version}-%{release}
# For building with KDE 4.12
# % kde4_akonadi_requires == "Requires: akonadi-runtime  >= 1.10.2 akonadi-runtime < 1.10.40" (on openSUSE 13.1)
Requires:       akonadi-runtime >= %( echo `rpm -q --queryformat '%{VERSION}' akonadi-runtime`)
%kde4_runtime_requires
%kde4_pimlibs_requires

%description -n %{name}-client-kde4
KDE 4 backend for SFLphone.
%endif

%changelog
