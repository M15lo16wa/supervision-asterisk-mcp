# Comptes PJSIP pour softphones

Généré par `scripts/create_pjsip_accounts.sh` le 2026-10-01.

## Paramètres de connexion

| Champ | Valeur |
| --- | --- |
| Domaine Asterisk | `10.0.2.15` |
| Transport | UDP |
| Port SIP | 5060 |
| Codecs | `ulaw` (PCMU), `alaw` (PCMA), `slin16` |
| Authentification | Login / mot de passe (USER) |
| Nom d'affichage | *qui que vous soyez* |

## Comptes

| Extension | Identifiant | Mot de passe | Rôle |
| --- | --- | --- | --- |

> Les mots de passe sont masqués. Pour les afficher :
> `./scripts/create_pjsip_accounts.sh --show-passwords`

> **Ne committez pas la version en clair de ce fichier.**

## Vérifier l'enregistrement

```bash
asterisk -rx 'pjsip show contacts'
asterisk -rx 'pjsip show endpoint 1001'
```

Un contact n'apparaît que **si le softphone s'est enregistré** : c'est le
critère de succès de la Phase 2. L'endpoint reste `Unavailable` tant que
rien n'est enregistré — c'est normal et attendu.
