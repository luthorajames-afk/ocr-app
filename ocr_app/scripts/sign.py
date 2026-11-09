"""Signature de code des binaires Windows avec signtool (règle R11).

R11 : sans signature, Windows SmartScreen affiche un avertissement qui
décourage l'installation. Ce script signe les exécutables produits par
``scripts/build.py`` (et les installeurs compilés par Inno Setup) quand
signtool et un certificat sont configurés ; sinon il affiche le guide des
étapes à suivre sans rien inventer ni télécharger (R4).

Détection de signtool.exe (dans cet ordre) :

1. variable d'environnement ``AOCRI_SIGNTOOL`` (chemin complet) ;
2. ``signtool`` dans le PATH ;
3. ``%ProgramFiles(x86)%\\Windows Kits\\10\\bin\\<version>\\x64\\signtool.exe``
   (SDK Windows installé) et son équivalent ``%ProgramFiles%``.

Configuration du certificat (variables d'environnement, aucun stockage local) :

- ``AOCRI_CERT_FILE``    : fichier .pfx (avec ``AOCRI_CERT_PASSWORD``) ;
- ``AOCRI_CERT_SHA1``    : empreinte SHA-1 d'un certificat du magasin
  utilisateur Windows (``certmgr.msc``) ;
- ``AOCRI_TIMESTAMP_URL``: serveur d'horodatage RFC 3161
  (défaut : ``http://timestamp.digicert.com``).

Usage :

    python scripts/sign.py bin1.exe bin2.exe [autres binaires...]

Codes de sortie : 0 = signé (ou rien à signer), 1 = échec de signature,
2 = prérequis manquants (guide affiché).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

DEFAULT_TIMESTAMP_URL = "http://timestamp.digicert.com"

EXIT_OK = 0
EXIT_SIGN_FAILED = 1
EXIT_PREREQUISITES_MISSING = 2

_GUIDE = """\
Signature de code (R11) — prérequis manquants, étapes à suivre :

1. Installer le Windows SDK (signtool.exe) ou vérifier son installation :
     « C:\\Program Files (x86)\\Windows Kits\\10\\bin\\<version>\\x64\\signtool.exe »

2. Obtenir un certificat de signature de code (aucun téléchargement automatique) :
     - certificat OV/EV auprès d'une autorité (DigiCert, Sectigo, GlobalSign…) ;
     - ou Azure Trusted Signing (Microsoft) ;
     - pour les tests internes uniquement, un certificat auto-signé :
         New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=Kriesty Solutions" \
           -CertStoreLocation Cert:\\CurrentUser\\My
       puis l'exporter en .pfx (certmgr.msc → Exporter → .pfx + mot de passe).

3. Configurer les variables d'environnement (une des deux options) :
     - AOCRI_CERT_FILE=<chemin .pfx> et AOCRI_CERT_PASSWORD=<mot de passe> ;
     - ou AOCRI_CERT_SHA1=<empreinte SHA-1 du certificat du magasin utilisateur>.
   Optionnel : AOCRI_TIMESTAMP_URL (défaut : {timestamp}), AOCRI_SIGNTOOL (chemin signtool).

4. Signer, puis vérifier :
     python scripts/sign.py dist\\worker_simple\\worker_simple\\worker_simple.exe
     signtool verify /pa /all <binaire>
"""


def find_signtool() -> Optional[str]:
    """Localise signtool.exe (env, PATH, Windows Kits), ou None."""
    override = os.environ.get("AOCRI_SIGNTOOL", "").strip()
    if override:
        if Path(override).is_file():
            return override
        return None
    which = shutil.which("signtool")
    if which:
        return which
    roots = []
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(variable)
        if base:
            roots.append(Path(base) / "Windows Kits" / "10" / "bin")
    for root in roots:
        try:
            versions = sorted(
                (path for path in root.iterdir() if path.is_dir()),
                reverse=True,
            )
        except OSError:
            continue
        for version_dir in versions:
            for arch in ("x64", "x86"):
                candidate = version_dir / arch / "signtool.exe"
                if candidate.is_file():
                    return str(candidate)
    return None


def certificate_args() -> Optional[List[str]]:
    """Arguments signtool du certificat, ou None si non configuré."""
    cert_file = os.environ.get("AOCRI_CERT_FILE", "").strip()
    if cert_file:
        args = ["/f", cert_file]
        password = os.environ.get("AOCRI_CERT_PASSWORD", "")
        if password:
            args += ["/p", password]
        return args
    cert_sha1 = os.environ.get("AOCRI_CERT_SHA1", "").strip()
    if cert_sha1:
        return ["/sha1", cert_sha1]
    return None


def prerequisites() -> tuple[Optional[str], Optional[List[str]]]:
    """(signtool, arguments certificat) — l'un ou l'autre peut valoir None."""
    return find_signtool(), certificate_args()


def sign_file(
    path: str | os.PathLike[str],
    signtool: Optional[str] = None,
    cert_args: Optional[List[str]] = None,
) -> int:
    """Signe un fichier ; retourne le code de sortie de signtool.

    Prérequis déjà absents -> EXIT_PREREQUISITES_MISSING (guide affiché
    par l'appelant). Le fichier signé doit exister.
    """
    tool = signtool or find_signtool()
    args = cert_args if cert_args is not None else certificate_args()
    if tool is None or args is None:
        return EXIT_PREREQUISITES_MISSING
    target = Path(path)
    if not target.is_file():
        print(f"sign : fichier introuvable : {target}", file=sys.stderr)
        return EXIT_SIGN_FAILED
    timestamp_url = os.environ.get("AOCRI_TIMESTAMP_URL", "").strip() or DEFAULT_TIMESTAMP_URL
    command = [
        tool,
        "sign",
        "/fd",
        "SHA256",
        "/tr",
        timestamp_url,
        "/td",
        "SHA256",
        *args,
        str(target),
    ]
    print(f"sign : {target}")
    completed = subprocess.run(command, check=False)
    return completed.returncode


def _print_guide() -> None:
    tool, cert = prerequisites()
    print(_GUIDE.format(timestamp=DEFAULT_TIMESTAMP_URL))
    if tool is None:
        print("signtool.exe : introuvable (ni AOCRI_SIGNTOOL, ni PATH, ni Windows Kits).")
    else:
        print(f"signtool.exe : {tool}")
    if cert is None:
        print(
            "Certificat : non configuré "
            "(AOCRI_CERT_FILE/AOCRI_CERT_PASSWORD ou AOCRI_CERT_SHA1 attendus)."
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/sign.py",
        description="Signature de code des binaires AOCRI avec signtool (R11).",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="binaires à signer (exe, dll) ; sans argument, l'état des prérequis est affiché",
    )
    args = parser.parse_args(argv)

    tool, cert_args = prerequisites()
    if tool is None or cert_args is None:
        _print_guide()
        return EXIT_PREREQUISITES_MISSING

    if not args.files:
        print("signtool et certificat détectés : rien à signer (passez des fichiers).")
        return EXIT_OK

    failures = 0
    for file in args.files:
        code = sign_file(file, signtool=tool, cert_args=cert_args)
        if code != EXIT_OK:
            failures += 1
    if failures:
        print(f"sign : {failures} fichier(s) non signé(s)", file=sys.stderr)
        return EXIT_SIGN_FAILED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
