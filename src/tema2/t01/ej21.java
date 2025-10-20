package tema2.t01;

import java.util.Scanner;
// Realiza un programa que permita la entrada de varios números y calcule su media.
public class ej21 {
    public static void main(String args[]) {
        Scanner sc = new Scanner(System.in);

        int numero;
        int suma = 0;
        int contador = 0;

        System.out.println("Introduce numeros (introduce 0 para terminar):");

        do {
            numero = sc.nextInt();
            if (numero != 0) {
                suma += numero;
                contador++;
            }
        } while (numero != 0);

        if (contador > 0) {
            double media = (double) suma / contador;
            System.out.println("La media de los numeros introducidos es: " + media);
        } else {
            System.out.println("No se introdujeron numeros.");
        }
    }
}
