package tema2.t04;
/*
Haz dos métodos, una para cifrar() y otro para descifrar() una cadena de caracteres.
Estos métodos reciben un String y retornan ese String ya cifrado o descifrado.
Para hacer el cifrado de un String, se cambia cada letra por la que está
dos puestos mas adelante( la a se cambia por la c), teniendo en cuenta que
el cambio es circular, es decir, la z se cambiará por la b.
El proceso de descifrado es el contrario.
Los caracteres que no sean alfabéticos no registrarán ningún cambio.
 */

public class ej08 {
    public static void main(String[] args) {
        String cadena = "holaz";
        System.out.println("La cadena es: " + cadena);

        // cifrar
        String cadenaCifrada = cifrar(cadena);
        System.out.println("La cadena cifrada sería: " + cadenaCifrada);
        // descifrar
        String cadenaDescifrada = descifrar(cadenaCifrada);
        System.out.println("La cadena descifrada sería: " + cadenaDescifrada);
    } static String cifrar(String cadena) {
        String cadenaCifrada = "";
        // Pasamos por todos los caracteres, y los vamos cifrando uno a uno
        for (int i = 0; i < cadena.length(); i++) {
            // Ciframos el caracter actual
            char caracter = cadena.charAt(i);
            if (caracter != 'y' && caracter != 'z') {
                char caracterCifrado = (char) (caracter + 2);
                // Lo metemos en la cadena cifrada
                cadenaCifrada = cadenaCifrada + caracterCifrado;
            } else if (caracter == 'y') {
                cadenaCifrada = cadenaCifrada + 'a';
            } else if (caracter == 'z') {
                cadenaCifrada = cadenaCifrada + 'b';
            }
        }
        return cadenaCifrada;
    }
    static String descifrar(String cadena) {
        String cadenaDescifrada = "";
        // Pasamos por todos los caracteres, y los vamos cifrando uno a uno
        for (int i = 0; i < cadena.length(); i++) {
            // Ciframos el caracter actual
            char caracter = cadena.charAt(i);
            if (caracter != 'a' && caracter != 'b') {
                char caracterDescifrado = (char) (caracter - 2);
                // Lo metemos en la cadena cifrada
                cadenaDescifrada = cadenaDescifrada + caracterDescifrado;
            } else if (caracter == 'a') {
                cadenaDescifrada = cadenaDescifrada + 'y';
            } else if (caracter == 'b') {
                cadenaDescifrada = cadenaDescifrada + 'z';
            }
        }
        return cadenaDescifrada;
    }
}
